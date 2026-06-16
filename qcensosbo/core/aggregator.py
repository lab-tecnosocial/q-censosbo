"""
Agrega microdatos censales por unidad geográfica.
Soporta: conteo, media, suma, y porcentaje de una categoría.

Los DataFrames provienen del motor DuckDB (query_engine); este módulo solo los
post-procesa (nombres geográficos, formato de geo_code), sin importar pandas.
"""

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
                  agg="__count__", category=None, remote=False,
                  departamento=None):
    """
    Agrega datos censales por unidad geográfica.

    - paths_or_urls: list[str]
    - nivel: "departamento" | "municipio"
    - variable: nombre de columna o "__count__"
    - agg: "__count__" | "mean" | "sum" | "pct_category"
    - category: valor de categoría (str) cuando agg="pct_category"
    - remote: ignorado (DuckDB lee local y remoto igual); se conserva por compat.
    - departamento: código "01"…"09" para filtrar la agregación a ese
      departamento (solo aplica a nivel municipal).

    Retorna DataFrame [geo_code, geo_nombre, valor].
    """
    from .query_engine import aggregate_geo

    df = aggregate_geo(paths_or_urls, nivel, variable, agg, category, departamento)

    pad = 2 if nivel == "departamento" else 6
    df["geo_code"] = df["geo_code"].astype(str).str.zfill(pad)

    if nivel == "departamento":
        df["geo_nombre"] = df["geo_code"].map(DEPT_NAMES).fillna(df["geo_code"])
    else:
        from .layer_builder import geo_nombres
        nombres = geo_nombres("municipio")
        df["geo_nombre"] = df["geo_code"].map(nombres).fillna(df["geo_code"])

    return df[["geo_code", "geo_nombre", "valor"]]


def resumen_nacional(paths_or_urls, variable="__count__", agg="__count__",
                     category=None, remote=False, departamento=None):
    """Valor de referencia (un escalar) del indicador, sin desagregar por geografía.

    `remote` se ignora (DuckDB lee local y remoto igual). Si se pasa
    `departamento`, el escalar es del departamento (referencia departamental)."""
    from .query_engine import aggregate_national
    return aggregate_national(paths_or_urls, variable, agg, category, departamento)


def get_columns(path_or_url, remote=False):
    """Lista columnas del parquet (solo lee el schema/footer), local o remoto."""
    from .query_engine import get_columns as qe_get_columns
    return qe_get_columns(path_or_url)


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
    from .query_engine import read_parquet_local_df

    # Los diccionarios son diminutos (<1 MB): se descargan una vez y se leen con
    # DuckDB. Si DuckDB aún se está instalando, read_parquet_local_df devuelve
    # None y NO cacheamos: se reintenta cuando el motor esté listo.
    path = download_codebook(anio)
    if not path:
        return {}  # NO cachear: reintentar cuando haya red / archivo

    try:
        df = read_parquet_local_df(path)
        if df is None:
            return {}  # DuckDB aún no listo: reintentar más tarde

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
    diccionario_etiquetas.parquet localmente con DuckDB.

    Si se pasa `tabla`, prioriza las etiquetas de esa tabla (algunas variables
    se repiten en varias tablas con códigos distintos). Si el filtro deja todo
    vacío, cae al resultado sin filtrar.
    """
    cache_key = (anio, variable, tabla)
    if cache_key in _val_labels_cache:
        return _val_labels_cache[cache_key]

    from .data_loader import download_labels_codebook
    from .query_engine import read_parquet_local_df

    path = download_labels_codebook(anio)
    if not path:
        return {}  # NO cachear: reintentar cuando haya red / archivo

    try:
        df = read_parquet_local_df(path)
        if df is None:
            return {}  # DuckDB aún no listo: reintentar más tarde

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


def agregar_expresion(paths_or_urls, nivel, sql_expr, departamento=None):
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
    df = aggregate_custom_sql(paths_or_urls, nivel, sql_expr, departamento)

    pad = 2 if nivel == "departamento" else 6
    df["geo_code"] = df["geo_code"].astype(str).str.zfill(pad)

    if nivel == "departamento":
        df["geo_nombre"] = df["geo_code"].map(DEPT_NAMES).fillna(df["geo_code"])
    else:
        from .layer_builder import geo_nombres
        nombres = geo_nombres("municipio")
        df["geo_nombre"] = df["geo_code"].map(nombres).fillna(df["geo_code"])

    return df[["geo_code", "geo_nombre", "valor"]]


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
