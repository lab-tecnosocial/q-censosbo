#!/usr/bin/env python3
"""
Reconciliación del universo de vivienda con las cifras oficiales del INE.

El resto de la suite comprueba que el plugin funcione: que el SQL se construya,
que las capas se dibujen, que la UI ofrezca lo que debe. Nada de eso garantiza
que los **números** sean los que publicó el INE, que es lo que un usuario va a
citar del mapa. Esto lo fija.

La tabla de viviendas del censo incluye registros de personas censadas *fuera* de
una vivienda —en la calle y en tránsito—, que el INE no cuenta como viviendas. El
plugin los descuenta con `core/universos.py`. Sin eso, publicaba 4.490.488
viviendas en 2024 en vez de las 4.480.201 oficiales, y el desfase se arrastraba a
cada mapa y a cada capa exportada.

No necesita QGIS: solo el núcleo (DuckDB). Sí necesita internet, porque consulta
los Parquet publicados en los releases de censosbo.

Uso:
    uv run --with duckdb --with pandas python scripts/qa_universos.py

Sale con código 1 si alguna comprobación falla.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from qcensosbo.core.aggregator import agregar_datos, resumen_nacional  # noqa: E402
from qcensosbo.core.query_engine import get_parquet_urls  # noqa: E402
from qcensosbo.core.universos import (  # noqa: E402
    TIPO_VIVIENDA, cobertura_vivienda, universo_sql,
)

fallos = 0


def comprobar(descripcion, condicion, detalle=None):
    global fallos
    ok = bool(condicion)
    if not ok:
        fallos += 1
    print(f"  {'ok   ' if ok else 'FALLA'} {descripcion}")
    if not ok and detalle:
        print(f"         → {detalle}")
    return ok


# ── 1. La condición SQL de cada censo ───────────────────────────────────────
# Offline: solo mira la tabla de códigos.
print("\n== La condición del universo por censo ==")

comprobar("la tabla cubre los cinco censos",
          sorted(TIPO_VIVIENDA) == [1976, 1992, 2001, 2012, 2024],
          f"años: {sorted(TIPO_VIVIENDA)}")
comprobar("2024 excluye calle (15) y tránsito (16)",
          TIPO_VIVIENDA[2024]["no_vivienda"] == [15, 16])
comprobar("1976 no excluye nada (no se preguntó por calle ni tránsito)",
          TIPO_VIVIENDA[1976]["no_vivienda"] == [])
comprobar("1976 no genera condición SQL", universo_sql(1976, "viviendas") is None)
comprobar("solo aplica a la tabla de viviendas",
          universo_sql(2024, "personas") is None
          and universo_sql(2024, "fichas") is None
          and universo_sql(2024, "viviendas") is not None)
# La columna se guarda como entero en unos censos y como texto en otros; sin
# TRY_CAST la comparación falla en silencio y no filtra nada.
comprobar("la condición castea antes de comparar",
          all("TRY_CAST" in (universo_sql(a, "viviendas") or "TRY_CAST")
              for a in TIPO_VIVIENDA))
comprobar("la nota de cobertura solo sale donde hay algo que declarar",
          cobertura_vivienda(2024, "viviendas")
          and cobertura_vivienda(2024, "personas") is None
          and cobertura_vivienda(1976, "viviendas") is None)

# ── 2. Los totales, contra las cifras oficiales ─────────────────────────────
# 2024 está contrastado con los tabulados del INE y con el geoportal. Los otros
# años aplican el mismo criterio del diccionario de cada censo; sus cifras fijan
# ese criterio y avisan si un release cambia los datos por debajo.
print("\n== Totales de vivienda contra las cifras oficiales ==")

OFICIAL = {2024: 4480201, 2012: 3159350, 2001: 2281022, 1992: 1701168,
           1976: 1158482}
CRUDO = {2024: 4490488, 2012: 3172321, 2001: 2290414, 1992: 1706107,
         1976: 1158482}

for anio in sorted(OFICIAL, reverse=True):
    urls = get_parquet_urls(anio, "viviendas")
    n = resumen_nacional(urls, universo_tabla=universo_sql(anio, "viviendas"))
    comprobar(f"{anio}: {OFICIAL[anio]:,} viviendas".replace(",", "."),
              n == OFICIAL[anio], f"devuelto: {n}")
    # Y que el descuento sea real: sin el universo salen más (salvo 1976).
    crudo = resumen_nacional(urls)
    comprobar(f"{anio}: la tabla cruda trae {CRUDO[anio] - OFICIAL[anio]} de más",
              crudo == CRUDO[anio], f"crudo devuelto: {crudo}")

# ── 3. El desglose, no solo el total ────────────────────────────────────────
# Un total puede cuadrar por compensación entre errores; un desglose completo no.
print("\n== Desglose del CPV-2024 ==")

urls24 = get_parquet_urls(2024, "viviendas")
u24 = universo_sql(2024, "viviendas")

df_area = agregar_datos(urls24, "departamento", universo_tabla=u24)
comprobar("los nueve departamentos suman el total oficial",
          len(df_area) == 9 and int(df_area["valor"].sum()) == 4480201,
          f"{len(df_area)} deptos, suma {int(df_area['valor'].sum())}")

df_tipo = agregar_datos(urls24, "departamento", variable="v01_tipoviv",
                        agg="pct_category", category="15", universo_tabla=u24)
comprobar("la categoría de calle ya no existe en el universo (0 % en todos)",
          float(df_tipo["valor"].fillna(0).max()) == 0.0,
          f"máximo: {df_tipo['valor'].max()}")

# ── 4. El cuadre con el geoportal, la otra fuente del INE ───────────────────
# Es la comprobación que desmontó la explicación anterior («el INE cuenta las
# viviendas de forma distinta en el geoportal y en los microdatos»): con el
# universo correcto las dos fuentes coinciden al registro.
print("\n== Cuadre con el geoportal (tabla de unidades) ==")

MUNICIPIOS = {"010101": "Sucre", "090101": "Cobija", "030101": "Cochabamba"}
urls_uni = get_parquet_urls(2024, "unidades")
for cod, nombre in MUNICIPIOS.items():
    micro = resumen_nacional(urls24, municipio=cod, universo_tabla=u24)
    geo = resumen_nacional(urls_uni, variable="viviendas", agg="sum",
                           municipio=cod)
    crudo = resumen_nacional(urls24, municipio=cod)
    comprobar(f"{nombre}: microdatos y geoportal coinciden",
              int(micro) == int(geo),
              f"microdatos={micro} geoportal={geo}")
    comprobar(f"{nombre}: con la tabla cruda NO coincidirían",
              int(crudo) != int(geo),
              f"crudo={crudo} geoportal={geo} (si son iguales, este municipio "
              f"no sirve de caso de prueba)")

print("\n" + "─" * 60)
if fallos:
    print(f"{fallos} comprobación(es) fallaron.")
    sys.exit(1)
print("Todas las comprobaciones pasaron.")
