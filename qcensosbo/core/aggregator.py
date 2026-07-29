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


def _con_nombres(df, nivel):
    """Añade la columna geo_nombre según el nivel geográfico.

    A nivel de manzano/comunidad el nombre no es único ni identifica la unidad
    (varios manzanos comparten el nombre de la zona), así que el resumen usa el
    propio código; el nombre legible lo pone la capa desde el parquet de
    geometrías.
    """
    if nivel == "departamento":
        df["geo_nombre"] = df["geo_code"].map(DEPT_NAMES).fillna(df["geo_code"])
    elif nivel == "municipio":
        from .layer_builder import geo_nombres
        nombres = geo_nombres("municipio")
        df["geo_nombre"] = df["geo_code"].map(nombres).fillna(df["geo_code"])
    else:
        df["geo_nombre"] = df["geo_code"]
    return df[["geo_code", "geo_nombre", "valor"]]


def agregar_datos(paths_or_urls, nivel, variable="__count__",
                  agg="__count__", category=None, remote=False,
                  departamento=None, municipio=None, area=None,
                  universo_tabla=None):
    """
    Agrega datos censales por unidad geográfica.

    - paths_or_urls: list[str]
    - nivel: "departamento" | "municipio" | "unidad" (manzano/comunidad)
    - variable: nombre de columna o "__count__"
    - agg: "__count__" | "mean" | "sum" | "pct_category"
    - category: valor de categoría (str) cuando agg="pct_category"
    - remote: ignorado (DuckDB lee local y remoto igual); se conserva por compat.
    - departamento: código "01"…"09" para filtrar la agregación a ese
      departamento (solo aplica a nivel municipal).
    - municipio: código nacional de 6 dígitos (obligatorio a nivel unidad).
    - area: "urbana" | "rural" (solo tablas de fichas).
    - universo_tabla: condición SQL que define qué filas de la tabla forman su
      universo, de `universos.universo_sql(anio, tabla)`. Imprescindible en la
      tabla de viviendas: sin ella se cuentan registros que no son viviendas. No
      es el «universo» del diccionario del INE (ver `get_var_universos()`).

    Retorna DataFrame [geo_code, geo_nombre, valor].
    """
    from .query_engine import aggregate_geo, pad_geo_code

    df = aggregate_geo(paths_or_urls, nivel, variable, agg, category,
                       departamento, municipio, area, universo_tabla)
    return _con_nombres(pad_geo_code(df, nivel), nivel)


def resumen_nacional(paths_or_urls, variable="__count__", agg="__count__",
                     category=None, remote=False, departamento=None,
                     municipio=None, area=None, universo_tabla=None):
    """Valor de referencia (un escalar) del indicador, sin desagregar por geografía.

    `remote` se ignora (DuckDB lee local y remoto igual). Los filtros geográficos
    acotan la referencia al territorio elegido (departamental o municipal)."""
    from .query_engine import aggregate_national
    return aggregate_national(paths_or_urls, variable, agg, category,
                              departamento, municipio, area, universo_tabla)


def get_columns(path_or_url, remote=False):
    """Lista columnas del parquet (solo lee el schema/footer), local o remoto."""
    from .query_engine import get_columns as qe_get_columns
    return qe_get_columns(path_or_url)


def _load_var_dict(anio, tabla=None):
    """
    Lee diccionario_variables.parquet una sola vez y retorna
    {variable: {"label", "tipo", "universo", "tema", "tema_etiqueta"}}, filtrado
    por tabla.

    Las tablas de fichas (manzano/comunidad) no tienen diccionario en el release:
    su catálogo viene empaquetado con el plugin (ver `fichas.py`).

    Cachea en memoria por (anio, tabla). NUNCA cachea cuando DuckDB no está
    disponible todavía (se instala en segundo plano): cachear {} ahí dejaría
    las descripciones vacías toda la sesión aunque el motor termine de instalar.

    `tipo` viene del diccionario con valores como 'categorica'/'numerica'/'texto'
    y es la fuente de verdad para decidir el tipo de variable.

    `universo`, `tema` y `tema_etiqueta` llegaron con censosbo 1.5.0 y pueden faltar
    (un diccionario más antiguo en caché): entonces quedan en `None` y quien las
    consuma simplemente no muestra nada. Nunca son obligatorias.
    """
    cache_key = (anio, tabla)
    if cache_key in _var_dict_cache:
        return _var_dict_cache[cache_key]

    from .fichas import TABLAS as TABLAS_FICHAS, catalogo
    if tabla in TABLAS_FICHAS:
        result = {r["variable"]: {"label": r["etiqueta"], "tipo": r["tipo"],
                                  "universo": r.get("universo") or None,
                                  "tema": r.get("tema") or None,
                                  "tema_etiqueta": r.get("tema_etiqueta") or None}
                  for r in catalogo(tabla)}
        _var_dict_cache[cache_key] = result
        return result

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
        # Añadidas en censosbo 1.5.0; ausentes en diccionarios anteriores.
        col_univ = _find_col(df, ["universo"])
        col_tema = _find_col(df, ["tema"])
        col_temal = _find_col(df, ["tema_etiqueta"])
        if not col_var:
            _var_dict_cache[cache_key] = {}
            return {}

        df = _filter_by_tabla(df, tabla)

        def texto(fila, col):
            """Valor de texto limpio, o None. Los nulos de parquet llegan como NaN,
            que `str()` convierte en la cadena 'nan' — de ahí la comprobación."""
            if not col:
                return None
            valor = fila[col]
            if valor is None or valor != valor:          # NaN != NaN
                return None
            valor = str(valor).strip()
            return valor if valor and valor.lower() not in ("nan", "none") else None

        result = {}
        for _, r in df.iterrows():
            name = str(r[col_var]).strip()
            label = str(r[col_desc]).strip() if col_desc else ""
            tipo = str(r[col_tipo]).strip().lower() if col_tipo else None
            result[name] = {
                "label": label,
                "tipo": tipo,
                "universo": texto(r, col_univ),
                "tema": texto(r, col_tema),
                "tema_etiqueta": texto(r, col_temal),
            }
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


# Universos que no siguen un patrón de edad. Los demás se derivan (ver
# `universo_legible`), así que un universo nuevo del INE no necesita tocar esto.
_UNIVERSOS_FIJOS = {
    "todas_personas":         "todas las personas",
    "todas_viviendas":        "todas las viviendas",
    "viviendas_presentes":    "viviendas con personas presentes",
    "viviendas_particulares": "viviendas particulares",
    "personas_emigrantes":    "personas emigrantes",
    "personas_fallecidas":    "personas fallecidas",
    "hogares":                "hogares",
}

_PLURALES = {"personas": "personas", "mujeres": "mujeres", "hombres": "hombres"}


def universo_legible(universo):
    """Traduce el slug de universo del diccionario a algo que se pueda leer.

    `personas_7_mas` → «personas de 7 años o más»; `mujeres_15_49` → «mujeres de 15
    a 49 años». Se resuelve por patrón y no con una lista cerrada, para que un
    universo nuevo en el diccionario salga razonable sin tocar el plugin.

    Devuelve `None` si no hay universo (diccionario anterior a censosbo 1.5.0, o
    una clave geográfica), y en ese caso la interfaz simplemente no dice nada.
    """
    if not universo:
        return None
    slug = str(universo).strip().lower()
    if slug in _UNIVERSOS_FIJOS:
        return _UNIVERSOS_FIJOS[slug]

    partes = slug.split("_")
    if len(partes) == 3 and partes[0] in _PLURALES and partes[2] == "mas" \
            and partes[1].isdigit():
        return f"{_PLURALES[partes[0]]} de {partes[1]} años o más"
    if len(partes) == 3 and partes[0] in _PLURALES \
            and partes[1].isdigit() and partes[2].isdigit():
        return f"{_PLURALES[partes[0]]} de {partes[1]} a {partes[2]} años"

    # Unidades geográficas (departamentos, cantones, zonas…) y cualquier cosa nueva.
    return slug.replace("_", " ")


def get_var_universos(anio, tabla=None):
    """{variable: universo legible} — a quién se le hizo cada pregunta.

    Viene del diccionario de censosbo 1.5.0+. Es el dato que evita el error más
    común del análisis censal: leer un porcentaje como si fuera sobre toda la
    población cuando la pregunta solo se hizo a un subgrupo (`nivel_edu` en 2024 se
    construyó sobre personas de 19 años o más, por ejemplo). Las variables sin
    universo declarado no aparecen en el resultado.
    """
    salida = {}
    for var, info in _load_var_dict(anio, tabla).items():
        legible = universo_legible(info.get("universo"))
        if legible:
            salida[var] = legible
    return salida


def get_var_temas(anio, tabla=None):
    """{variable: (tema, etiqueta del tema)} para filtrar el catálogo por tema."""
    salida = {}
    for var, info in _load_var_dict(anio, tabla).items():
        tema = info.get("tema")
        if tema:
            salida[var] = (tema, info.get("tema_etiqueta") or
                           tema.replace("_", " ").capitalize())
    return salida


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

    from .fichas import TABLAS as TABLAS_FICHAS
    if tabla in TABLAS_FICHAS:
        # Los indicadores de ficha son conteos, no categorías. Las dos columnas
        # con dominio propio se resuelven aquí, sin descargar nada.
        result = {
            "area":  {"1": "Urbana", "2": "Rural"},
            "ficha": {"true": "Con ficha", "false": "Sin ficha"},
        }.get(variable, {})
        _val_labels_cache[cache_key] = result
        return result

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


def agregar_expresion(paths_or_urls, nivel, sql_expr, departamento=None,
                      municipio=None, area=None, universo_tabla=None):
    """
    Agrega datos con una expresión SQL agregada.

    La usan el modo SQL avanzado del panel y los indicadores de ficha (que se
    declaran como expresión en `fichas.py`). Requiere DuckDB.
    """
    from .query_engine import aggregate_custom_sql, duckdb_available, pad_geo_code

    if not duckdb_available():
        raise RuntimeError(
            "La expresión SQL personalizada requiere DuckDB.\n"
            "Espera a que termine la instalación automática."
        )
    df = aggregate_custom_sql(paths_or_urls, nivel, sql_expr, departamento,
                              municipio, area, universo_tabla)
    return _con_nombres(pad_geo_code(df, nivel), nivel)


def resumen_expresion(paths_or_urls, sql_expr, departamento=None,
                      municipio=None, area=None, universo_tabla=None):
    """Valor de referencia de una expresión agregada, sin desagregar por geografía."""
    from .query_engine import national_custom_sql
    return national_custom_sql(paths_or_urls, sql_expr, departamento,
                               municipio, area, universo_tabla)


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
