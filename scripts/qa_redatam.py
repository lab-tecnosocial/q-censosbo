#!/usr/bin/env python3
"""
Reconciliación con REDATAM: que el plugin dé los números que publica el INE.

`qa_universos.py` fija el universo de la tabla de viviendas —qué filas son un
caso—. Esto fija lo otro que decide una cifra publicable: **el denominador de un
porcentaje**, que es donde se pierden los números.

No hay un denominador y otro, hay tres, y confundir dos de ellos es el error más
común del análisis censal:

  1. Todos los registros consultados — la población del territorio, con la
     pregunta hecha o no.
  2. Los registros CON DATO en la pregunta — lo que totaliza una tabulación de
     REDATAM, y lo que usa el plugin en «% de categoría».
  3. El de una tasa publicada — el que el INE declara para *ese* indicador. El
     plugin no tiene catálogo de tasas precocinadas, así que aquí no aplica.

El plugin usa el (2), que es el que reproduce REDATAM. Estas comprobaciones lo
fijan contra una tabulación concreta del WebServer del INE, no contra su propia
coherencia interna: si un cambio en el motor moviera el denominador, el 35,11 %
de Santiváñez dejaría de salir y esto falla.

No necesita QGIS: solo el núcleo (DuckDB). Sí necesita internet, porque consulta
los Parquet publicados en los releases de censosbo.

Uso:
    uv run --with duckdb --with pandas python scripts/qa_redatam.py

Sale con código 1 si alguna comprobación falla.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from qcensosbo.core.aggregator import resumen_nacional  # noqa: E402
from qcensosbo.core.query_engine import (  # noqa: E402
    get_parquet_urls, variable_coverage,
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


# ── 1. El caso que destapó el problema del denominador ──────────────────────
#
# Pregunta 45 del CPV-2024 (¿atendió cultivos?) en Santiváñez, Cochabamba
# (030702). Contrastado consulta a consulta contra el WebServer del INE
# (redatam.ine.gob.bo/redbol): los CONTEOS coinciden uno a uno, y lo que no
# coincidía era el total —y con él todos los porcentajes—.
#
#            plugin      REDATAM (INE)
#   Sí        1.548      1.548 — 35,11 %
#   No        2.724      2.724 — 61,78 %
#   Sin esp.    137        137 —  3,11 %
#   Sin dato  3.531      no aparece
#   TOTAL     7.940      4.409
#
# Las dos lecciones que fija este bloque:
#
#   · «Sin especificar» (código 9) ES una respuesta: es un código del
#     cuestionario y REDATAM lo cuenta en el total. Descontarlo no acerca al
#     número oficial, lo aleja (4.272 ≠ 4.409).
#   · «Sin dato» NO es una categoría: es la ausencia de la pregunta. El plugin
#     lo deja fuera del denominador porque `COUNT(columna)` ignora los nulos, y
#     eso es exactamente lo que hace REDATAM.
print("\n== La pregunta 45 en Santiváñez, contra el WebServer del INE ==")

SANTIVANEZ = "030702"
CONTEOS = {"1": 1548, "2": 2724, "9": 137}      # Sí · No · Sin especificar
PORCENTAJES = {"1": 35.11, "2": 61.78, "9": 3.11}
CON_DATO, REGISTROS = 4409, 7940

urls24 = get_parquet_urls(2024, "personas", departamento="03")

n_total, n_validos = variable_coverage(
    urls24, "p45_agro", departamento="03", municipio=SANTIVANEZ)

comprobar(f"el denominador es {CON_DATO:,} casos con dato".replace(",", "."),
          n_validos == CON_DATO, f"devuelto: {n_validos}")
comprobar(f"y la tabla trae {REGISTROS:,} registros".replace(",", "."),
          n_total == REGISTROS, f"devuelto: {n_total}")
comprobar("los «Sin dato» son 3.531 y quedan fuera del denominador",
          (n_total or 0) - (n_validos or 0) == 3531,
          f"diferencia: {(n_total or 0) - (n_validos or 0)}")

for cat, esperado in CONTEOS.items():
    n = resumen_nacional(urls24, "p45_agro", "__count__", cat,
                         departamento="03", municipio=SANTIVANEZ)
    comprobar(f"categoría {cat}: {esperado:,} casos".replace(",", "."),
              int(n or 0) == esperado, f"devuelto: {n}")

for cat, esperado in PORCENTAJES.items():
    pct = resumen_nacional(urls24, "p45_agro", "pct_category", cat,
                           departamento="03", municipio=SANTIVANEZ)
    comprobar(f"categoría {cat}: {esperado} % (cifra publicada por el INE)",
              pct is not None and abs(float(pct) - esperado) < 0.01,
              f"devuelto: {pct}")

# Que el denominador sea la SUMA de las categorías con dato, «Sin especificar»
# incluido. Es la comprobación que impide "arreglar" el porcentaje descontando
# la no respuesta declarada: 1.548 + 2.724 + 137 = 4.409, y sin el 137 daría
# 4.272, que no es la cifra de nadie.
comprobar("«Sin especificar» está DENTRO del denominador (137 incluidos)",
          sum(CONTEOS.values()) == CON_DATO,
          f"suma de categorías: {sum(CONTEOS.values())}")

# Las DOS medidas, cada una con su denominador. Ninguna está mal; lo que estaría
# mal es no saber cuál se pidió: aquí van 15,6 puntos de diferencia sobre el mismo
# dato. `pct_category` es el que reproduce al INE; `pct_total` es el que responde
# «qué parte de los habitantes atiende cultivos», contando a quien no recibió la
# pregunta.
PCT_TOTAL_CAT1 = round(100.0 * CONTEOS["1"] / REGISTROS, 2)      # 19,50 %
pct_total = resumen_nacional(urls24, "p45_agro", "pct_total", "1",
                             departamento="03", municipio=SANTIVANEZ)
comprobar(f"«% sobre todos los registros» da {PCT_TOTAL_CAT1} %",
          pct_total is not None and abs(float(pct_total) - PCT_TOTAL_CAT1) < 0.01,
          f"devuelto: {pct_total}")
comprobar("y las dos medidas NO coinciden (por eso hay que declarar cuál es)",
          abs(float(pct_total) - PORCENTAJES["1"]) > 15)

# Y que el universo declarado del diccionario NO explica los «Sin dato»: filtrar
# por «personas de 7 años o más» deja 7.089 casos, no 4.409. De los 3.531 «Sin
# dato» solo 851 son menores de 7 años; los otros 2.680 están dentro del
# universo declarado y la pregunta no les llegó porque el cuestionario la salta
# (la 45 solo se hace a quien respondió «no» en la 43 y en la 44). De ahí la
# regla de la casa: el metadato explica, los datos calculan.
UNIVERSO_7_MAS = "TRY_CAST(p26_edad AS INTEGER) >= 7"
n_7_mas = resumen_nacional(urls24, departamento="03", municipio=SANTIVANEZ,
                           universo_tabla=UNIVERSO_7_MAS)
comprobar("el universo declarado (7 años o más) deja 7.089, no 4.409",
          int(n_7_mas or 0) == 7089, f"devuelto: {n_7_mas}")
comprobar("→ filtrar por el universo declarado NO daría el denominador oficial",
          int(n_7_mas or 0) != CON_DATO)

# ── 2. Los saltos del cuestionario explican los «Sin dato» ──────────────────
#
# El aviso del plugin decía «Pregunta aplicada a: personas de 7 años o más»
# leyendo solo el campo `universo` del diccionario, y eso miente por omisión: de
# los 3.531 «Sin dato» de la p45 en Santiváñez, solo 851 son menores de 7 años.
# Los otros 2.680 están DENTRO del universo declarado y la pregunta no les llegó
# porque el cuestionario la salta.
#
# Estas comprobaciones fijan las dos mitades: que las condiciones leídas del
# metadato son la p43 y la p44 con el código «Sí», y que ese flujo predice los
# «Sin dato» caso por caso, sin residuo.
print("\n== Los saltos del cuestionario, contra los datos ==")

from qcensosbo.core.docs_vars import (  # noqa: E402
    condiciones_previas, frase_condiciones,
)

cond = condiciones_previas(2024, "p45_agro", "personas")
comprobar("la p45 está condicionada por dos preguntas anteriores",
          len(cond) == 2, f"leídas: {[c['num'] for c in cond]}")
comprobar("y son la 43 y la 44",
          [c["num"] for c in cond] == [43, 44],
          f"leídas: {[c['num'] for c in cond]}")
comprobar("el código que dispara el salto es el 1 («Sí») en las dos",
          all(c["respuestas"] == [(1, "Sí")] for c in cond),
          f"respuestas: {[c['respuestas'] for c in cond]}")

frase = frase_condiciones(2024, "p45_agro", "personas") or ""
comprobar("la frase se redacta en negativo (la no respuesta también llega)",
          frase.startswith("no a quienes respondieron"), f"frase: {frase[:60]}")

# Una pregunta cuyo salto cae ANTES no condiciona: la p45 salta a la 47, así que
# no dice nada sobre la 47 misma, solo sobre la 46.
comprobar("un salto que no pasa por encima de la pregunta no la condiciona",
          45 in [c["num"] for c in condiciones_previas(2024, "p46_dest", "personas")]
          and 45 not in [c["num"] for c in
                         condiciones_previas(2024, "p47_otro", "personas")])
comprobar("una variable sin condiciones no inventa ninguna",
          frase_condiciones(2024, "p26_edad", "personas") is None
          and frase_condiciones(2024, "v17_tenencia", "viviendas") is None)

# Y ahora contra los datos: el universo declarado más el salto derivado del
# metadato dan los 3.531 «Sin dato» exactos, sin residuo.
SIN_DATO, MENORES, POR_SALTO = 3531, 851, 2680
SIETE_MAS = "TRY_CAST(p26_edad AS INTEGER) >= 7"
SALTARON = ("(TRY_CAST(p43_pago AS INTEGER) = 1 "
            "OR TRY_CAST(p44_nego AS INTEGER) = 1)")


def sin_dato(extra=None):
    cond_sql = "p45_agro IS NULL" + (f" AND {extra}" if extra else "")
    return int(resumen_nacional(urls24, departamento="03",
                                municipio=SANTIVANEZ,
                                universo_tabla=cond_sql) or 0)


comprobar(f"«Sin dato» en la p45: {SIN_DATO:,}".replace(",", "."),
          sin_dato() == SIN_DATO, f"devuelto: {sin_dato()}")
comprobar(f"  fuera del universo declarado (menores de 7): {MENORES}",
          sin_dato(f"NOT {SIETE_MAS}") == MENORES)
comprobar("  dentro del universo, saltados por la p43/p44: "
          + f"{POR_SALTO:,}".replace(",", "."),
          sin_dato(f"{SIETE_MAS} AND {SALTARON}") == POR_SALTO)
comprobar("  residuo sin explicar: 0 (la predicción es caso por caso)",
          sin_dato(f"{SIETE_MAS} AND NOT {SALTARON}") == 0,
          f"residuo: {sin_dato(f'{SIETE_MAS} AND NOT {SALTARON}')}")

# ── 3. Las celdas frágiles se cuentan, no se suprimen ───────────────────────
#
# El plugin marca en vez de ocultar: el INE publica los microdatos completos —de
# ahí salen estos Parquet—, así que suprimir una celda de tres casos no protegería
# a nadie, mientras que la advertencia de fiabilidad sí falta.
#
# El caso: `p52_pais_mov_cod` (país donde trabaja) en Pando. Es la variable con
# menos cobertura del censo (99,9 % «Sin dato» dentro de su universo declarado), y
# a nivel municipal deja porcentajes calculados sobre 0 a 4 casos. Aquí se fija
# también la distinción que evita inflar el aviso: una unidad con CERO casos no es
# un dato frágil, es un dato ausente.
print("\n== Celdas frágiles en el peor caso del censo ==")

from qcensosbo.core.aggregator import agregar_datos  # noqa: E402
from qcensosbo.core.layer_builder import (  # noqa: E402
    CASOS_FRAGIL, unidades_fragiles,
)

comprobar("el umbral de fragilidad son 5 casos", CASOS_FRAGIL == 5)

urls_pando = get_parquet_urls(2024, "personas", departamento="09")
df_pando = agregar_datos(urls_pando, "municipio", "p52_pais_mov_cod",
                         "pct_category", "1", departamento="09")

comprobar("el motor trae el tamaño de muestra de cada unidad",
          "casos" in df_pando.columns, f"columnas: {list(df_pando.columns)}")
comprobar("Pando tiene 15 municipios en el resultado",
          len(df_pando) == 15, f"devuelto: {len(df_pando)}")

n_fragiles, n_con_valor = unidades_fragiles(df_pando)
comprobar("6 de los 9 municipios con valor lo calculan sobre menos de 5 casos",
          (n_fragiles, n_con_valor) == (6, 9),
          f"devuelto: {n_fragiles} de {n_con_valor}")
comprobar("las unidades sin casos NO cuentan como frágiles (son sin dato)",
          n_con_valor < len(df_pando),
          f"con valor: {n_con_valor} de {len(df_pando)}")

# Y que una expresión libre no invente un tamaño de muestra que no puede conocer.
comprobar("sin columna `casos` el aviso no se inventa nada",
          unidades_fragiles(df_pando[["geo_code", "valor"]]) == (0, 0))

# ── 4. Los totales de población de los cinco censos ─────────────────────────
#
# Un total se copia tal cual, así que es lo primero que hay que poder citar. Los
# cuatro primeros son las cifras publicadas por el INE. 1976 es el total de la
# base de microdatos: la cifra publicada de ese censo es 4.613.486, y la base
# trae 67 registros menos. La diferencia viene de la fuente —está igual en
# `censosbo`— y NO está explicada; queda anotada aquí para que se note si algún
# día cambia, en vez de descubrirla en un mapa.
print("\n== Totales de población de los cinco censos ==")

POBLACION = {2024: 11365333, 2012: 10059856, 2001: 8274325, 1992: 6420792,
             1976: 4613419}
PUBLICADO_1976 = 4613486

for anio in sorted(POBLACION, reverse=True):
    n = resumen_nacional(get_parquet_urls(anio, "personas"))
    nota = " (microdatos; publicado 4.613.486)" if anio == 1976 else ""
    comprobar(f"{anio}: {POBLACION[anio]:,} personas{nota}".replace(",", "."),
              int(n or 0) == POBLACION[anio], f"devuelto: {n}")

comprobar("la diferencia de 1976 sigue siendo de 67 registros",
          PUBLICADO_1976 - POBLACION[1976] == 67)

print("\n" + "─" * 60)
if fallos:
    print(f"{fallos} comprobación(es) fallaron.")
    sys.exit(1)
print("Todas las comprobaciones pasaron.")
