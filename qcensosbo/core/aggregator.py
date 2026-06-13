"""
Agrega microdatos censales por unidad geográfica.
Soporta: conteo, media, suma, y porcentaje de una categoría.
"""

import pandas as pd

# Caché en memoria para evitar consultas repetidas en la misma sesión
_var_dict_cache = {}          # {(anio, tabla): {variable: {"label","tipo"}}}
_val_labels_cache = {}        # {(anio, variable, tabla): {codigo: etiqueta}}

DEPT_NAMES = {
    "01": "Chuquisaca", "02": "La Paz",      "03": "Cochabamba",
    "04": "Oruro",      "05": "Potosí",      "06": "Tarija",
    "07": "Santa Cruz", "08": "Beni",         "09": "Pando",
}

# La columna 'entidad'/'tabla' del diccionario indica a qué tabla pertenece la
# variable. Los nombres cambian entre censos (p.ej. 1976 usa 'poblacion' en vez
# de 'PERSONA'), así que cada tabla del plugin se mapea a TODOS sus posibles
# nombres. La comparación es case-insensitive.
TABLA_ENTIDADES = {
    "personas":   {"PERSONA", "POBLACION"},
    "viviendas":  {"VIVIENDA"},
    "emigracion": {"EMIGRACION"},
    "mortalidad": {"MORTALIDAD"},
}


def agregar_datos(paths_or_urls, nivel, variable="__count__",
                  agg="__count__", category=None, remote=False):
    """
    Agrega datos censales por unidad geográfica.

    - paths_or_urls: list[str]
    - nivel: "departamento" | "municipio"
    - variable: nombre de columna o "__count__"
    - agg: "__count__" | "mean" | "sum" | "pct_category"
    - category: valor de categoría (str) cuando agg="pct_category"
    - remote: True si son URLs remotas

    Retorna DataFrame [geo_code, geo_nombre, valor].
    """
    from .query_engine import aggregate_remote, aggregate_local, duckdb_available

    if remote and duckdb_available():
        df = aggregate_remote(paths_or_urls, nivel, variable, agg, category)
    else:
        df = aggregate_local(paths_or_urls, nivel, variable, agg, category)

    pad = 2 if nivel == "departamento" else 6
    df["geo_code"] = df["geo_code"].astype(str).str.zfill(pad)

    if nivel == "departamento":
        df["geo_nombre"] = df["geo_code"].map(DEPT_NAMES).fillna(df["geo_code"])
    else:
        df["geo_nombre"] = df["geo_code"]

    return df[["geo_code", "geo_nombre", "valor"]]


def resumen_nacional(paths_or_urls, variable="__count__", agg="__count__",
                     category=None, remote=False):
    """Valor nacional (un escalar) del indicador, sin desagregar por geografía."""
    from .query_engine import (
        aggregate_national_remote, aggregate_national_local, duckdb_available
    )
    if remote and duckdb_available():
        return aggregate_national_remote(paths_or_urls, variable, agg, category)
    return aggregate_national_local(paths_or_urls, variable, agg, category)


def get_columns(path_or_url, remote=False):
    """Lista columnas del parquet (solo lee el schema/footer)."""
    from .query_engine import get_columns_from_path, get_columns_remote, duckdb_available

    if remote and duckdb_available():
        return get_columns_remote(path_or_url)
    if not remote:
        try:
            return get_columns_from_path(path_or_url)
        except Exception:
            return []
    return []


def _load_var_dict(anio, tabla=None):
    """
    Lee diccionario_variables.parquet una sola vez y retorna
    {variable: {"label": str, "tipo": str|None}}, filtrado por tabla.

    Cachea en memoria por (anio, tabla). NUNCA cachea cuando DuckDB no está
    disponible todavía (se instala en segundo plano): cachear {} ahí dejaría
    las descripciones vacías toda la sesión aunque el motor termine de instalar.

    `tipo` viene del diccionario con valores como 'categorica'/'numerica'/'texto'
    y es la fuente de verdad para decidir el tipo de variable.
    """
    cache_key = (anio, tabla)
    if cache_key in _var_dict_cache:
        return _var_dict_cache[cache_key]

    from .data_loader import download_codebook

    # Los diccionarios son diminutos (<1 MB): se descargan una vez y se leen con
    # pyarrow. Así NO dependemos de que DuckDB esté listo (evita que falten las
    # etiquetas en datos cacheados localmente mientras el motor aún instala).
    path = download_codebook(anio)
    if not path:
        return {}  # NO cachear: reintentar cuando haya red / archivo

    try:
        import pyarrow.parquet as pq
        df = pq.read_table(path).to_pandas()

        col_var  = _find_col(df, ["variable", "nombre_variable", "var", "nombre"])
        col_desc = _find_col(df, ["label", "etiqueta_variable", "descripcion",
                                  "descripcion_variable", "etiqueta", "desc"])
        col_tipo = _find_col(df, ["tipo", "type", "tipo_variable"])
        if not col_var:
            _var_dict_cache[cache_key] = {}
            return {}

        df = _filter_by_tabla(df, tabla)

        result = {}
        for _, r in df.iterrows():
            name = str(r[col_var]).strip()
            label = str(r[col_desc]).strip() if col_desc else ""
            tipo = str(r[col_tipo]).strip().lower() if col_tipo else None
            result[name] = {"label": label, "tipo": tipo}
        _var_dict_cache[cache_key] = result  # cachear éxito
        return result
    except Exception:
        return {}


def get_var_descriptions(anio, tabla=None):
    """Retorna dict {variable: descripcion} (desde diccionario_variables)."""
    return {v: info["label"] for v, info in _load_var_dict(anio, tabla).items()
            if info.get("label")}


def get_var_types(anio, tabla=None):
    """
    Retorna dict {variable: tipo} con tipo en {'categorica','numerica','texto'}.
    Es la fuente de verdad para clasificar variables (mejor que el heurístico).
    """
    return {v: info["tipo"] for v, info in _load_var_dict(anio, tabla).items()
            if info.get("tipo")}


def get_value_labels(anio, variable, tabla=None):
    """
    Retorna dict {codigo_str: etiqueta_str} para una variable categórica, leyendo
    diccionario_etiquetas.parquet localmente con pyarrow (sin depender de DuckDB).

    Si se pasa `tabla`, prioriza las etiquetas de esa tabla (algunas variables
    se repiten en varias tablas con códigos distintos). Si el filtro deja todo
    vacío, cae al resultado sin filtrar.
    """
    cache_key = (anio, variable, tabla)
    if cache_key in _val_labels_cache:
        return _val_labels_cache[cache_key]

    from .data_loader import download_labels_codebook

    path = download_labels_codebook(anio)
    if not path:
        return {}  # NO cachear: reintentar cuando haya red / archivo

    try:
        import pyarrow.parquet as pq
        df = pq.read_table(path).to_pandas()

        col_var = _find_col(df, ["variable", "var", "nombre_variable"])
        col_val = _find_col(df, ["valor", "codigo", "code", "value"])
        col_lbl = _find_col(df, ["etiqueta", "label", "descripcion", "desc"])
        if not all([col_var, col_val, col_lbl]):
            _val_labels_cache[cache_key] = {}
            return {}

        sub = df[df[col_var].astype(str).str.lower() == str(variable).lower()]

        # Priorizar la tabla pedida; si queda vacío, usar todas
        col_ent = _find_col(df, ["entidad", "tabla"])
        entidades = TABLA_ENTIDADES.get(tabla) if tabla else None
        if col_ent and entidades:
            ents = {e.upper() for e in entidades}
            f = sub[sub[col_ent].astype(str).str.upper().isin(ents)]
            if len(f):
                sub = f

        result = {str(r[col_val]): str(r[col_lbl]) for _, r in sub.iterrows()}
        _val_labels_cache[cache_key] = result  # cachear éxito (incluso vacío)
        return result
    except Exception:
        return {}


def agregar_expresion(paths_or_urls, nivel, sql_expr):
    """
    Agrega datos con una expresión SQL personalizada del usuario.
    Requiere DuckDB (consulta remota o local vía HTTP).
    """
    from .query_engine import aggregate_custom_sql, duckdb_available

    if not duckdb_available():
        raise RuntimeError(
            "La expresión SQL personalizada requiere DuckDB.\n"
            "Espera a que termine la instalación automática."
        )
    df = aggregate_custom_sql(paths_or_urls, nivel, sql_expr)

    pad = 2 if nivel == "departamento" else 6
    df["geo_code"] = df["geo_code"].astype(str).str.zfill(pad)

    if nivel == "departamento":
        df["geo_nombre"] = df["geo_code"].map(DEPT_NAMES).fillna(df["geo_code"])
    else:
        df["geo_nombre"] = df["geo_code"]

    return df[["geo_code", "geo_nombre", "valor"]]


def clear_caches():
    """Limpia los cachés en memoria y los diccionarios descargados a disco.

    Borrar los diccionarios locales fuerza a re-descargarlos: útil cuando se
    actualiza un release de GitHub con nuevas variables/etiquetas/tipos.
    """
    _var_dict_cache.clear()
    _val_labels_cache.clear()
    try:
        from .query_engine import _schema_cache
        _schema_cache.clear()
    except Exception:
        pass
    # Borrar los parquet de diccionario en caché local (se re-descargan solos)
    try:
        from .data_loader import cache_dir
        for f in cache_dir().rglob("diccionario_*.parquet"):
            try:
                f.unlink()
            except Exception:
                pass
    except Exception:
        pass


def _find_col(df, candidates):
    """Retorna el primer nombre de columna que coincide (case-insensitive)."""
    cols_lower = {c.lower(): c for c in df.columns}
    for name in candidates:
        if name.lower() in cols_lower:
            return cols_lower[name.lower()]
    return None


def _filter_by_tabla(df, tabla):
    """
    Filtra el diccionario a las filas de la tabla pedida usando la columna
    'entidad'/'tabla'. Tolerante: si no hay columna, no hay mapeo, o el filtro
    deja el resultado vacío (nombres inesperados), devuelve el df sin filtrar.
    """
    if not tabla:
        return df
    col_ent = _find_col(df, ["entidad", "tabla"])
    entidades = TABLA_ENTIDADES.get(tabla)
    if not col_ent or not entidades:
        return df
    mask = df[col_ent].astype(str).str.upper().isin({e.upper() for e in entidades})
    filtered = df[mask]
    return filtered if len(filtered) else df
