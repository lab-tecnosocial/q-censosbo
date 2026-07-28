"""
Datos agregados del CPV-2024 por manzano urbano y comunidad rural (las "fichas").

Es el nivel geográfico más fino del censo: 268.604 unidades censales frente a los
339 municipios del resto del plugin. Viven en un release aparte
(`data-fichas-v1.0.0`) y su modelo es distinto al de los microdatos:

  - `unidad.parquet` — las 268.604 unidades con población, viviendas y si el INE
    libera su ficha.
  - `ficha.parquet`  — 194 indicadores ya agregados (conteos) para las 150.744
    unidades que el INE sí libera. El resto se reserva por poca población, así
    que en el mapa salen sin dato (92% de la población queda cubierta).
  - `geo_manzano_dep{dd}.parquet` — polígonos de los manzanos, partidos por
    departamento. `geo_comunidad.parquet` — las comunidades rurales, que el INE
    publica como PUNTOS, no polígonos.

Todas las tablas traen `codigo` (identificador de la unidad, NO jerárquico) más
`idep`/`iprov`/`imun` desnormalizados; el join datos↔geometría es por `codigo`.

Este módulo es el único lugar que conoce este dataset: el catálogo de
indicadores (con su expresión SQL y su denominador) y la obtención de las
geometrías. El motor de consulta solo ejecuta las expresiones que salen de aquí.
"""

import csv
from pathlib import Path

# Release propio, separado de los microdatos por año.
TAG = "data-fichas-v1.0.0"

TABLAS = ("fichas", "unidades")

# Mismo dominio que la variable `area` de los microdatos (y que el paquete R).
AREAS = {"urbana": 1, "rural": 2}

# Nombre legible de cada bloque temático, para agrupar el selector de variables.
BLOQUES = {
    "poblacion":    "Población",
    "educacion":    "Educación",
    "salud_lugar":  "Salud · atención",
    "salud_seguro": "Salud · seguro",
    "nacimiento":   "Lugar de nacimiento",
    "residencia":   "Residencia habitual",
    "ocupacion":    "Ocupación",
    "actividad":    "Actividad económica",
    "vivienda":     "Vivienda",
    "servicios":    "Servicios básicos",
    "tic":          "TIC",
    "material":     "Materiales",
    "hacinamiento": "Hacinamiento",
    "hogar":        "Tipo de hogar",
    "unidad":       "Unidad censal",
}

DICC_PATH = Path(__file__).parent.parent / "data" / "dicc_fichas.csv"

_catalogo_cache = {}


def catalogo(tabla):
    """Indicadores disponibles para 'fichas' o 'unidades', en orden de bloque.

    Lee el CSV empaquetado con la librería estándar: no necesita red, ni DuckDB,
    ni pandas, así el selector de variables se llena al instante y funciona sin
    conexión (a diferencia de los otros años, cuyo diccionario se descarga).

    Retorna lista de dicts {variable, etiqueta, bloque, expr, denominador, tipo}.
    """
    if tabla in _catalogo_cache:
        return _catalogo_cache[tabla]
    filas = []
    try:
        with open(DICC_PATH, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                if r.get("tabla") == tabla:
                    filas.append(r)
    except OSError:
        filas = []
    _catalogo_cache[tabla] = filas
    return filas


def indicador(tabla, variable):
    """Fila del catálogo de una variable, o None si no está."""
    for r in catalogo(tabla):
        if r["variable"] == variable:
            return r
    return None


def tiene_porcentaje(tabla, variable):
    """True si la variable tiene un total de bloque que sirva de denominador."""
    r = indicador(tabla, variable)
    return bool(r and r.get("denominador"))


def sql_valor(tabla, variable, medida="total"):
    """Expresión SQL agregada para el campo `valor`.

    Los 194 indicadores son conteos, así que:
      - `total`      → SUM(expr): a nivel unidad es el propio valor de la ficha;
        a nivel municipal o departamental suma las unidades del territorio.
      - `porcentaje` → razón de sumas contra el total del bloque, que es la forma
        correcta de agregar una proporción (no el promedio de las proporciones).
    """
    r = indicador(tabla, variable)
    if not r:
        raise ValueError(f"Indicador desconocido en la tabla '{tabla}': {variable}")
    expr = r["expr"] or r["variable"]
    if medida == "porcentaje":
        den = r.get("denominador")
        if not den:
            raise ValueError(
                f"«{r['etiqueta']}» es un total del bloque: no tiene denominador "
                "para calcular un porcentaje."
            )
        return f"ROUND(100.0 * SUM({expr}) / NULLIF(SUM({den}), 0), 2)"
    return f"SUM({expr})"


# ─────────────────────────────────────────────────────────────────────────────
# Geometrías
# ─────────────────────────────────────────────────────────────────────────────

def geo_file(area, idep):
    """Archivo de geometrías del release para un área y departamento."""
    if AREAS.get(area) == AREAS["rural"]:
        return "geo_comunidad.parquet"      # archivo nacional único
    return f"geo_manzano_dep{str(idep).zfill(2)}.parquet"


def geometrias(municipio, area="urbana", progress_cb=None):
    """Geometrías de las unidades de un municipio: [(codigo, nombre, wkb), …].

    `municipio` es el código nacional de 6 dígitos (idep+iprov+imun), el mismo
    que usa el resto del plugin. `area` es "urbana" (manzanos, polígonos) o
    "rural" (comunidades, puntos).

    A diferencia de los parquet de datos —que DuckDB consulta en remoto sin
    descargar— estos SÍ se descargan y cachean en `~/.censosbo_qgis/fichas/`:
    un mapa necesita la geometría completa del municipio, y el usuario suele
    hacer varios mapas del mismo, así que bajar el archivo una vez (0,3–6,7 MB)
    sale más barato que releerlo por HTTP en cada consulta.
    """
    from .data_loader import download_ficha_file
    from .query_engine import duckdb_available, _make_con, _close

    code = str(municipio).zfill(6)
    idep, iprov, imun = code[:2], code[2:4], code[4:6]

    if not duckdb_available():
        raise RuntimeError(
            "El motor de consulta (DuckDB) aún no está disponible: espera a que "
            "termine de instalarse y vuelve a intentarlo."
        )

    path = download_ficha_file(geo_file(area, idep), progress_cb=progress_cb)
    if not path:
        raise RuntimeError(
            f"No se pudieron descargar las geometrías ({geo_file(area, idep)}). "
            "Revisa tu conexión a internet."
        )

    con = _make_con([path])
    try:
        return con.execute(
            "SELECT codigo, nombre, geometria FROM read_parquet(?) "
            "WHERE TRY_CAST(idep AS INTEGER) = ? "
            "  AND TRY_CAST(iprov AS INTEGER) = ? "
            "  AND TRY_CAST(imun AS INTEGER) = ?",
            [path, int(idep), int(iprov), int(imun)],
        ).fetchall()
    finally:
        _close(con)
