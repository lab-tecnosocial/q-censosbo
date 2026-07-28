#!/usr/bin/env python3
"""
Fase 1 de la prueba headless: captura datos REALES en un fixture.

Se ejecuta con un Python que tenga `duckdb` y `pandas` (no el de QGIS: su
`_duckdb.so` está firmado para el bundle y macOS lo rechaza fuera de él). Guarda
en `dist/qa_fixture.pkl` los schemas, diccionarios, agregaciones y geometrías que
`qa_headless.py` necesita luego para ejercitar la UI sin red ni motor.

Uso:
    uv run --with duckdb --with pandas python scripts/qa_fixture.py
    # o con cualquier venv que tenga duckdb+pandas:
    python scripts/qa_fixture.py

Requiere internet: consulta los releases del paquete censosbo.
"""

import os
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import qcensosbo.core.query_engine as qe          # noqa: E402

# El plugin registra un os._exit al importar duckdb (evita el SIGABRT de QGIS al
# cerrar); aquí estorbaría, porque mataría el script antes de escribir el pickle.
qe._register_hard_exit = lambda: None
qe._hard_exit_registered = True

from qcensosbo.core.data_loader import get_tables_for_year          # noqa: E402
from qcensosbo.core.query_engine import (                           # noqa: E402
    get_parquet_urls, get_columns, variable_coverage, distinct_values)
from qcensosbo.core.aggregator import (                             # noqa: E402
    get_var_descriptions, get_var_types, get_value_labels,
    agregar_datos, resumen_nacional, agregar_expresion, resumen_expresion)
from qcensosbo.core import fichas                                   # noqa: E402

DEST = ROOT / "dist" / "qa_fixture.pkl"

# Municipio de referencia: Cochabamba capital, que tiene manzanos y comunidades.
MUNICIPIO = "030101"

# (anio, tabla, nivel, variable, agg, category, depto, municipio, area)
ESCENARIOS = [
    (2024, "personas", "departamento", "__count__", "__count__", None, None, None, None),
    (2024, "personas", "departamento", "p26_edad", "mean", None, None, None, None),
    (2024, "personas", "departamento", "p25_sexo", "pct_category", "2", None, None, None),
    (2024, "personas", "departamento", "p25_sexo", "mode", None, None, None, None),
    (2024, "personas", "departamento", "p39_tipoest", "pct_category", "9", None, None, None),
    (2024, "personas", "municipio", "p26_edad", "mean", None, None, None, None),
    (2024, "personas", "municipio", "p26_edad", "mean", None, "03", None, None),
    (2024, "viviendas", "departamento", "__count__", "__count__", None, None, None, None),
    (1976, "personas", "departamento", "__count__", "__count__", None, None, None, None),
]

# (tabla, variable, medida, nivel, municipio, area)
ESCENARIOS_FICHA = [
    ("fichas", "serv_agua_caneria", "porcentaje", "departamento", None, None),
    ("fichas", "serv_agua_caneria", "porcentaje", "unidad", MUNICIPIO, None),
    ("fichas", "serv_agua_caneria", "total", "departamento", None, None),
    ("fichas", "pob_total_h", "total", "departamento", None, None),
    ("unidades", "personas", "total", "unidad", MUNICIPIO, None),
]


def main():
    fx = {"cols": {}, "descs": {}, "types": {}, "labels": {},
          "agg": {}, "nat": {}, "cobertura": {}, "distinct": {}}

    print("1/5 Schemas y diccionarios de cada (año, tabla)…")
    for anio in (2024, 2012, 2001, 1992, 1976):
        for _, tabla in get_tables_for_year(anio):
            urls = get_parquet_urls(anio, tabla)
            cols = get_columns(urls[0])
            if not cols:
                raise SystemExit(
                    f"✗ {anio}/{tabla}: el parquet remoto no devolvió columnas. "
                    "¿Sin internet, o cambió el release?")
            fx["cols"][(anio, tabla)] = cols
            fx["descs"][(anio, tabla)] = get_var_descriptions(anio, tabla)
            fx["types"][(anio, tabla)] = get_var_types(anio, tabla)
            print(f"    {anio} {tabla:11} {len(cols):3} columnas, "
                  f"{len(fx['types'][(anio, tabla)]):3} tipos")

    print("2/5 Etiquetas de valores (2024 personas y viviendas)…")
    for tabla in ("personas", "viviendas"):
        for v, t in fx["types"][(2024, tabla)].items():
            if t in ("categorica", "texto"):
                fx["labels"][(2024, v, tabla)] = get_value_labels(2024, v, tabla)

    print("3/5 Agregaciones de microdatos…")
    for esc in ESCENARIOS:
        anio, tabla, nivel, var, agg, cat, dep, mun, area = esc
        urls = get_parquet_urls(anio, tabla, dep)
        key = esc + (None,)      # el último hueco es sql_expr
        fx["agg"][key] = agregar_datos(urls, nivel, var, agg, cat,
                                       departamento=dep, municipio=mun, area=area)
        fx["nat"][key] = resumen_nacional(urls, var, agg, cat, departamento=dep,
                                          municipio=mun, area=area)
        fx["cobertura"][key] = (variable_coverage(urls, var, departamento=dep,
                                                 municipio=mun, area=area)
                                if agg == "pct_category" else (None, None))
        print(f"    {anio} {tabla} {nivel} {var} {agg}: {len(fx['agg'][key])} filas")

    print("4/5 Indicadores de ficha…")
    for tabla, var, med, nivel, mun, area in ESCENARIOS_FICHA:
        urls = get_parquet_urls(2024, tabla)
        sql = fichas.sql_valor(tabla, var, med)
        key = (2024, tabla, nivel, var, med, None, None, mun, area, sql)
        fx["agg"][key] = agregar_expresion(urls, nivel, sql, municipio=mun, area=area)
        fx["nat"][key] = resumen_expresion(urls, sql, municipio=mun, area=area)
        fx["cobertura"][key] = (None, None)
        print(f"    {tabla} {var} {med} {nivel}: {len(fx['agg'][key])} filas")

    print("5/5 Valores distintos y geometrías…")
    urls_p = get_parquet_urls(2024, "personas")
    # Todas las categóricas que el diccionario de etiquetas NO cubre: son las que
    # ejercitan el respaldo de «valores distintos del parquet». Unas se rescatan
    # (dominio pequeño) y otras no (dominio demasiado grande para ser categórico).
    candidatas = [v for v, t in fx["types"][(2024, "personas")].items()
                  if t in ("categorica", "texto")
                  and not fx["labels"].get((2024, v, "personas"))]
    for v in candidatas + ["p57b_uhnacan", "p25_sexo"]:
        if (2024, "personas", v) in fx["distinct"]:
            continue
        fx["distinct"][(2024, "personas", v)] = distinct_values(urls_p, v)
        print(f"    distinct {v}: {len(fx['distinct'][(2024, 'personas', v)])}")
    sin_ninguna = [v for v in candidatas
                   if not fx["distinct"].get((2024, "personas", v))]
    fx["vars_sin_categorias"] = sin_ninguna
    print(f"    sin ninguna categoría posible: {sin_ninguna}")
    for area in ("urbana", "rural"):
        g = fichas.geometrias(MUNICIPIO, area)
        # Recortado: la prueba solo necesita ejercitar el camino, no 12.000 polígonos.
        fx[f"geoms_{MUNICIPIO}_{area}"] = g[:400]
        print(f"    geometrías {area}: {len(g)} (se guardan {min(len(g), 400)})")
    fx["municipio_prueba"] = MUNICIPIO

    DEST.parent.mkdir(exist_ok=True)
    with open(DEST, "wb") as f:
        pickle.dump(fx, f)
    print(f"\n✓ Fixture en {DEST}")
    # os._exit: duckdb aborta en el apagado del intérprete (ver _register_hard_exit).
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
