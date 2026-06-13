"""
Motor de consulta para archivos parquet del censosbo.

Estrategia de velocidad:
  1. DuckDB remoto: consulta directamente sobre HTTPS sin descargar el archivo.
     Parquet almacena estadísticas en el footer; DuckDB hace HTTP range requests
     y lee solo las columnas/rowgroups necesarios. Un COUNT(*) por depto puede
     transferir <2 MB en vez de 500 MB.
  2. Pyarrow local con proyección de columnas: fallback si DuckDB no está listo.
     Lee solo las columnas necesarias (~50x más rápido que leer el archivo completo).

DuckDB se instala automáticamente en la primera apertura del panel (no requiere
acción del usuario más allá de instalar el plugin).
"""

import subprocess
import sys
import threading
from pathlib import Path

from .data_loader import (
    BASE_URL, RELEASES, TABLE_FILES, DEPT_CODES,
    cache_dir, _year_cache_dir, _download_file,
)

# ─────────────────────────────────────────────────────────────────────────────
# DuckDB: detección y auto-instalación
# ─────────────────────────────────────────────────────────────────────────────

_duckdb = None
_duckdb_checked = False
_schema_cache = {}   # {(url, "__cols__"): {col_lower: col_real}}

NO_MUNICIPIO_MSG = (
    "Este censo/tabla no tiene nivel municipal disponible "
    "(p. ej. el censo de 1976 usa cantón, no municipio). Usa nivel Departamental."
)


def _try_duckdb():
    global _duckdb, _duckdb_checked
    if _duckdb_checked:
        return _duckdb
    _duckdb_checked = True
    try:
        import duckdb
        _duckdb = duckdb
    except ImportError:
        _duckdb = None
    return _duckdb


def duckdb_available():
    return _try_duckdb() is not None


def install_duckdb(status_cb=None, done_cb=None):
    """
    Instala duckdb en el Python de QGIS de forma silenciosa.
    Llamar en un QThread para no bloquear la UI.

    status_cb(str): callback con texto de estado
    done_cb(bool):  callback al terminar — True si instaló correctamente
    """
    global _duckdb, _duckdb_checked

    # Ya está instalado
    if _try_duckdb():
        if done_cb:
            done_cb(True)
        return

    if status_cb:
        status_cb("Instalando DuckDB (solo la primera vez)…")

    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "duckdb", "-q",
             "--disable-pip-version-check"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=120,
        )
        # Forzar re-importación
        _duckdb_checked = False
        _duckdb = None
        _try_duckdb()
        success = _duckdb is not None
    except Exception:
        success = False

    if status_cb:
        status_cb("DuckDB instalado correctamente." if success else
                  "No se pudo instalar DuckDB (modo descarga local activo).")
    if done_cb:
        done_cb(success)


# ─────────────────────────────────────────────────────────────────────────────
# URL helpers
# ─────────────────────────────────────────────────────────────────────────────

def get_parquet_urls(anio, tabla, departamento=None):
    """Retorna lista de URLs remotas para (anio, tabla, departamento opcional)."""
    tag = RELEASES[anio]
    if anio == 2024 and tabla == "personas":
        codes = [departamento] if departamento else DEPT_CODES
        return [f"{BASE_URL}/{tag}/persona_dep{c}.parquet" for c in codes]
    filename = TABLE_FILES.get((anio, tabla))
    if not filename:
        raise ValueError(f"Tabla '{tabla}' no disponible para año {anio}")
    return [f"{BASE_URL}/{tag}/{filename}"]


def get_first_url(anio, tabla):
    """URL del primer archivo (útil para leer schema)."""
    return get_parquet_urls(anio, tabla)[0]


# ─────────────────────────────────────────────────────────────────────────────
# Schema (lista de columnas)
# ─────────────────────────────────────────────────────────────────────────────

def get_columns_from_path(path):
    """Lee el schema de un parquet local (solo el footer, instantáneo)."""
    import pyarrow.parquet as pq
    return pq.read_schema(path).names


def get_columns_remote(url):
    """
    Lee el schema de un parquet remoto con DuckDB (solo footer, ~1-3 seg).
    Retorna lista de nombres de columnas o [] si falla.
    """
    duckdb = _try_duckdb()
    if not duckdb:
        return []
    con = None
    try:
        con = duckdb.connect()
        con.execute("INSTALL httpfs; LOAD httpfs;")
        result = con.execute(f"DESCRIBE SELECT * FROM read_parquet('{url}') LIMIT 0").fetchall()
        return [row[0] for row in result]
    except Exception:
        return []
    finally:
        _close(con)


# ─────────────────────────────────────────────────────────────────────────────
# Aggregation
# ─────────────────────────────────────────────────────────────────────────────

def _geo_col(nivel):
    return "idep" if nivel == "departamento" else "imun"


def _pad_width(nivel):
    return 2 if nivel == "departamento" else 6


def _make_con():
    duckdb = _try_duckdb()
    if not duckdb:
        raise RuntimeError("DuckDB no disponible.")
    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    return con


def _close(con):
    """Cierra una conexión DuckDB de forma segura.

    Crucial para evitar el crash al cerrar QGIS: si quedan conexiones (y por
    tanto instancias de base de datos) vivas, el destructor estático de la
    librería DuckDB se ejecuta durante el apagado del intérprete e intenta
    tomar el GIL después de que Python ya finalizó → SIGABRT.
    """
    if con is not None:
        try:
            con.close()
        except Exception:
            pass


def _from_clause(urls):
    urls_sql = ", ".join(f"'{u}'" for u in urls)
    return f"read_parquet([{urls_sql}])"


def _is_dept_partitioned(urls):
    """True para archivos 2024 particionados por depto (persona_dep01..09.parquet)."""
    return any("persona_dep" in str(u) for u in urls)


# Candidatos de nombre de columna para geografía (case-insensitive)
_GEO_CANDIDATES = {
    "departamento": ["idep", "dpto", "dep", "departamento", "cod_dep", "codep",
                     "iddep", "depto", "id_dep"],
    "provincia":    ["iprov", "prov", "provincia", "cod_prov", "idprov"],
    "municipio":    ["imun", "mun", "municipio", "cod_mun", "comun", "munc",
                     "idmun", "id_mun"],
}


def _describe_cols(con, url):
    """Retorna {nombre_lower: nombre_real} de las columnas del parquet (cacheado)."""
    cache_key = (url, "__cols__")
    if cache_key in _schema_cache:
        return _schema_cache[cache_key]
    cols = {}
    try:
        rows = con.execute(
            f"DESCRIBE SELECT * FROM read_parquet('{url}') LIMIT 0"
        ).fetchall()
        cols = {r[0].lower(): r[0] for r in rows}
    except Exception:
        pass
    _schema_cache[cache_key] = cols
    return cols


def _pick_col(cols_lower, candidates):
    """Primer nombre real cuyo lower coincide con un candidato."""
    for c in candidates:
        if c in cols_lower:
            return cols_lower[c]
    return None


def _detect_geo_col(con, url, nivel):
    """Nombre real de la columna geográfica del nivel, con fallback al estándar."""
    cols = _describe_cols(con, url)
    return _pick_col(cols, _GEO_CANDIDATES.get(nivel, [])) or _geo_col(nivel)


def _build_geo_parts(con, urls, nivel):
    """
    Retorna (src_clause, geo_select_sql, group_col).

    Camino preferido (todos los censos ya traen geografía en la tabla): construye
    el código a partir de las columnas reales — departamento = idep(2);
    municipio = idep(2)+iprov(2)+imun(2), igual que el GeoJSON.

    Fallback: archivos 2024 particionados que aún no tengan columna idep → extrae
    el departamento del nombre de archivo virtual de DuckDB.
    """
    src = _from_clause(urls)
    cols = _describe_cols(con, urls[0])
    idep = _pick_col(cols, _GEO_CANDIDATES["departamento"])

    # Fallback para archivos particionados sin columna idep
    if not idep and _is_dept_partitioned(urls):
        urls_sql = ", ".join(f"'{u}'" for u in urls)
        src = f"read_parquet([{urls_sql}], filename=true)"
        dep = "LPAD(regexp_extract(filename, 'persona_dep(\\d+)', 1), 2, '0')"
        if nivel == "departamento":
            return src, f"{dep} AS geo_code", "geo_code"
        iprov = _pick_col(cols, _GEO_CANDIDATES["provincia"]) or "iprov"
        imun  = _pick_col(cols, _GEO_CANDIDATES["municipio"]) or "imun"
        geo_select = (f"CONCAT({dep}, LPAD(CAST({iprov} AS VARCHAR), 2, '0'), "
                      f"LPAD(CAST({imun} AS VARCHAR), 2, '0')) AS geo_code")
        return src, geo_select, "geo_code"

    if nivel == "departamento":
        geo = idep or _geo_col("departamento")
        geo_select = f"LPAD(CAST({geo} AS VARCHAR), 2, '0') AS geo_code"
    else:
        iprov = _pick_col(cols, _GEO_CANDIDATES["provincia"])
        imun  = _pick_col(cols, _GEO_CANDIDATES["municipio"])
        if idep and iprov and imun:
            geo_select = (f"CONCAT(LPAD(CAST({idep} AS VARCHAR), 2, '0'), "
                          f"LPAD(CAST({iprov} AS VARCHAR), 2, '0'), "
                          f"LPAD(CAST({imun} AS VARCHAR), 2, '0')) AS geo_code")
        elif imun:
            geo_select = f"CAST({imun} AS VARCHAR) AS geo_code"
        else:
            raise ValueError(NO_MUNICIPIO_MSG)
    return src, geo_select, "geo_code"


def _cat_filter_sql(var_expr, category):
    """Condición SQL para '= categoría', robusta a ceros a la izquierda.

    Los códigos del diccionario vienen sin relleno ('1') y los datos a veces con
    relleno ('001'); si el código es numérico, comparamos como enteros.
    """
    cat = str(category)
    catn = normalize_code(cat)
    core = catn[1:] if catn[:1] == "-" else catn
    if core.isdigit():
        return f"TRY_CAST({var_expr} AS BIGINT) = {int(catn)}"
    return f"CAST({var_expr} AS VARCHAR) = '{cat.replace(chr(39), chr(39) * 2)}'"


def aggregate_remote(urls, nivel, variable="__count__", agg="__count__", category=None):
    """
    Agrega datos sobre parquet remoto con DuckDB.

    Maneja tanto archivos históricos (con columna idep/imun) como archivos
    particionados del 2024 (sin columna idep, geo extraído del nombre de archivo).
    Retorna DataFrame [geo_code, valor].
    """
    con = _make_con()
    src, geo_select, group = _build_geo_parts(con, urls, nivel)

    if agg == "__count__":
        if category is not None:
            sql = f"""
                SELECT {geo_select}, COUNT(*) AS valor
                FROM {src}
                WHERE {_cat_filter_sql(variable, category)}
                GROUP BY {group}
            """
        else:
            sql = f"""
                SELECT {geo_select}, COUNT(*) AS valor
                FROM {src} GROUP BY {group}
            """
    elif agg == "mean":
        sql = f"""
            SELECT {geo_select},
                   ROUND(AVG(TRY_CAST({variable} AS DOUBLE)), 4) AS valor
            FROM {src} WHERE {variable} IS NOT NULL GROUP BY {group}
        """
    elif agg == "sum":
        sql = f"""
            SELECT {geo_select},
                   SUM(TRY_CAST({variable} AS DOUBLE)) AS valor
            FROM {src} WHERE {variable} IS NOT NULL GROUP BY {group}
        """
    elif agg == "median":
        sql = f"""
            SELECT {geo_select},
                   ROUND(MEDIAN(TRY_CAST({variable} AS DOUBLE)), 4) AS valor
            FROM {src} WHERE {variable} IS NOT NULL GROUP BY {group}
        """
    elif agg == "std":
        sql = f"""
            SELECT {geo_select},
                   ROUND(STDDEV(TRY_CAST({variable} AS DOUBLE)), 4) AS valor
            FROM {src} WHERE {variable} IS NOT NULL GROUP BY {group}
        """
    elif agg == "mode":
        sql = f"""
            SELECT {geo_select},
                   MODE({variable}) AS valor
            FROM {src} WHERE {variable} IS NOT NULL GROUP BY {group}
        """
    elif agg == "pct_category" and category is not None:
        sql = f"""
            SELECT {geo_select},
                   ROUND(100.0 * COUNT(CASE WHEN {_cat_filter_sql(variable, category)}
                                            THEN 1 END) / NULLIF(COUNT(*), 0), 2) AS valor
            FROM {src} GROUP BY {group}
        """
    else:
        sql = f"""
            SELECT {geo_select}, COUNT(*) AS valor
            FROM {src} GROUP BY {group}
        """

    try:
        df = con.execute(sql).df()
    finally:
        _close(con)
    df["geo_code"] = df["geo_code"].astype(str).str.zfill(_pad_width(nivel))
    return df


def _national_value_sql(variable, agg, category):
    """Expresión escalar (sin GROUP BY) para el valor nacional según la agregación."""
    v = variable
    if agg == "__count__":
        if category is not None:
            return f"COUNT(*) FILTER (WHERE {_cat_filter_sql(v, category)})"
        return "COUNT(*)"
    if agg == "mean":
        return f"ROUND(AVG(TRY_CAST({v} AS DOUBLE)), 4)"
    if agg == "sum":
        return f"SUM(TRY_CAST({v} AS DOUBLE))"
    if agg == "median":
        return f"ROUND(MEDIAN(TRY_CAST({v} AS DOUBLE)), 4)"
    if agg == "std":
        return f"ROUND(STDDEV(TRY_CAST({v} AS DOUBLE)), 4)"
    if agg == "mode":
        return f"MODE({v})"
    if agg == "pct_category" and category is not None:
        return (f"ROUND(100.0 * COUNT(*) FILTER (WHERE {_cat_filter_sql(v, category)}) "
                f"/ NULLIF(COUNT(*), 0), 2)")
    return "COUNT(*)"


def aggregate_national_remote(urls, variable="__count__", agg="__count__", category=None):
    """Valor nacional (un solo escalar, sin desagregar por geografía) vía DuckDB."""
    con = _make_con()
    src = _from_clause(urls)
    expr = _national_value_sql(variable, agg, category)
    where = "" if agg in ("__count__", "pct_category") else f" WHERE {variable} IS NOT NULL"
    try:
        row = con.execute(f"SELECT {expr} AS v FROM {src}{where}").fetchone()
        return row[0] if row else None
    except Exception:
        return None
    finally:
        _close(con)


def _read_col_local(paths, variable):
    import pyarrow.parquet as pq
    import pandas as pd
    parts = [pq.read_table(p, columns=[variable]).to_pandas()[variable] for p in paths]
    return pd.concat(parts, ignore_index=True) if parts else pd.Series(dtype="object")


def aggregate_national_local(paths, variable="__count__", agg="__count__", category=None):
    """Valor nacional (un escalar) desde parquet locales."""
    import pyarrow.parquet as pq
    import pandas as pd
    try:
        if agg == "__count__" and category is None:
            return int(sum(pq.read_metadata(p).num_rows for p in paths))
        s = _read_col_local(paths, variable)
        if agg == "__count__":
            cn = normalize_code(category)
            return int((s.astype(str).map(normalize_code) == cn).sum())
        if agg == "pct_category" and category is not None:
            cn = normalize_code(category)
            m = (s.astype(str).map(normalize_code) == cn).sum()
            return round(100.0 * m / len(s), 2) if len(s) else 0.0
        if agg == "mode":
            md = s.dropna().mode()
            return md.iloc[0] if len(md) else None
        num = pd.to_numeric(s, errors="coerce").dropna()
        if agg == "mean":
            return round(float(num.mean()), 4) if len(num) else None
        if agg == "sum":
            return float(num.sum())
        if agg == "median":
            return round(float(num.median()), 4) if len(num) else None
        if agg == "std":
            return round(float(num.std()), 4) if len(num) else None
        return int(len(s))
    except Exception:
        return None


def _local_geo_cols(paths, nivel):
    """
    Detecta las columnas geográficas reales del primer parquet local y retorna
    (idep, iprov, imun, partitioned). `partitioned` es True solo si no hay idep
    y los archivos son los particionados de 2024 (fallback por nombre de archivo).
    """
    import pyarrow.parquet as pq
    low = {c.lower(): c for c in pq.read_schema(paths[0]).names}
    def pick(cands):
        for c in cands:
            if c in low:
                return low[c]
        return None
    idep  = pick(_GEO_CANDIDATES["departamento"])
    iprov = pick(_GEO_CANDIDATES["provincia"])
    imun  = pick(_GEO_CANDIDATES["municipio"])
    partitioned = (not idep) and all(Path(p).name.startswith("persona_dep") for p in paths)
    return idep, iprov, imun, partitioned


def aggregate_local(paths, nivel, variable="__count__", agg="__count__", category=None):
    """
    Agrega datos desde parquet locales con proyección de columnas.

    Construye geo_code desde las columnas reales (departamento = idep;
    municipio = idep+iprov+imun). Fallback: archivos 2024 particionados sin
    columna idep → departamento del nombre de archivo.
    """
    import pyarrow.parquet as pq
    import pandas as pd

    idep, iprov, imun, partitioned = _local_geo_cols(paths, nivel)

    if nivel == "departamento":
        geo_cols = [idep] if idep else []
    else:
        if not imun and not partitioned:
            raise ValueError(NO_MUNICIPIO_MSG)
        geo_cols = [c for c in (idep, iprov, imun) if c]

    needs_var = (variable not in (None, "__count__")) and (agg != "__count__" or category is not None)
    read_cols = list(dict.fromkeys(geo_cols + ([variable] if needs_var else [])))

    parts = []
    for p in paths:
        if read_cols:
            df = pq.read_table(p, columns=read_cols).to_pandas()
        else:
            # Solo conteo total por depto particionado: no hace falta leer columnas
            df = pd.DataFrame(index=range(pq.read_metadata(p).num_rows))

        if partitioned:
            dep = Path(p).stem.replace("persona_dep", "").zfill(2)
            if nivel == "departamento":
                df["geo_code"] = dep
            else:
                df["geo_code"] = (dep
                    + (df[iprov].astype(str).str.zfill(2) if iprov else "00")
                    + (df[imun].astype(str).str.zfill(2) if imun else "00"))
        elif nivel == "departamento":
            df["geo_code"] = df[idep].astype(str).str.zfill(2) if idep else ""
        else:
            df["geo_code"] = (df[idep].astype(str).str.zfill(2)
                              + df[iprov].astype(str).str.zfill(2)
                              + df[imun].astype(str).str.zfill(2))
        parts.append(df)

    combined = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=["geo_code"])
    gc = "geo_code"

    # Comparación de categoría robusta a ceros a la izquierda ("001" == "1")
    catn = normalize_code(category) if category is not None else None
    def eq_cat(s):
        return s.astype(str).map(normalize_code) == catn

    if agg == "__count__":
        if category is not None:
            combined = combined[eq_cat(combined[variable])]
        return combined.groupby(gc).size().reset_index(name="valor")

    num = lambda s: pd.to_numeric(s, errors="coerce")
    if agg == "mean":
        result = combined.groupby(gc)[variable].apply(lambda s: num(s).mean())
    elif agg == "sum":
        result = combined.groupby(gc)[variable].apply(lambda s: num(s).sum())
    elif agg == "median":
        result = combined.groupby(gc)[variable].apply(lambda s: num(s).median())
    elif agg == "std":
        result = combined.groupby(gc)[variable].apply(lambda s: num(s).std())
    elif agg == "mode":
        result = combined.groupby(gc)[variable].apply(
            lambda s: s.mode().iloc[0] if len(s.mode()) > 0 else None)
    elif agg == "pct_category" and category is not None:
        result = combined.groupby(gc)[variable].apply(
            lambda s: round(100.0 * eq_cat(s).sum() / len(s), 2) if len(s) else 0.0)
    else:
        return combined.groupby(gc).size().reset_index(name="valor")

    result = result.reset_index(name="valor")
    result["geo_code"] = result["geo_code"].astype(str)
    return result


def normalize_code(s):
    """Normaliza un código para comparar datos con el diccionario de etiquetas.

    Los datos a veces traen códigos con ceros a la izquierda ("001") mientras el
    diccionario los guarda sin relleno ("1"). Normalizamos los códigos numéricos
    a su forma entera para que coincidan; los no numéricos quedan igual.
    """
    s = str(s).strip()
    core = s[1:] if s[:1] == "-" else s
    return str(int(s)) if core.isdigit() else s


# ─────────────────────────────────────────────────────────────────────────────
# Custom SQL expression
# ─────────────────────────────────────────────────────────────────────────────

def aggregate_custom_sql(urls, nivel, sql_expr):
    """
    Agrega datos con una expresión SQL libre del usuario.

    sql_expr es solo la fórmula para el campo 'valor', por ejemplo:
        "AVG(p26_edad)"
        "100.0 * SUM(CASE WHEN p25_sexo = 1 THEN 1 END) / COUNT(*)"

    El plugin envuelve la expresión con el GROUP BY geográfico.
    Retorna DataFrame [geo_code, valor].
    """
    con = _make_con()
    src, geo_select, group = _build_geo_parts(con, urls, nivel)

    sql = f"""
        SELECT {geo_select},
               ({sql_expr}) AS valor
        FROM {src}
        GROUP BY {group}
    """
    try:
        df = con.execute(sql).df()
    finally:
        _close(con)
    df["geo_code"] = df["geo_code"].astype(str).str.zfill(_pad_width(nivel))
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Parallel download
# ─────────────────────────────────────────────────────────────────────────────

def cleanup():
    """
    Libera el módulo DuckDB mientras Python aún está activo.
    Llamar desde plugin.unload() para evitar el crash de DuckDB al cerrar QGIS
    (DuckDB intenta liberar recursos después de que Python ya comenzó a apagarse).
    """
    global _duckdb, _duckdb_checked, _schema_cache
    _schema_cache.clear()
    # Forzar la recolección de cualquier conexión DuckDB pendiente ANTES de que
    # Python finalice. Así se destruyen las instancias de base de datos mientras
    # el GIL sigue vivo y se evita el SIGABRT del destructor estático al salir.
    try:
        import gc
        gc.collect()
    except Exception:
        pass
    if _duckdb is not None:
        try:
            import sys
            if "duckdb" in sys.modules:
                del sys.modules["duckdb"]
        except Exception:
            pass
        _duckdb = None
    _duckdb_checked = False


def download_parallel(anio, tabla, departamento=None, progress_cb=None):
    """
    Descarga archivos parquet en paralelo (hasta 4 hilos simultáneos).
    Retorna lista de rutas locales.
    """
    from .data_loader import get_cached_paths, is_cached
    import concurrent.futures

    # Si ya están en caché, retornar inmediatamente
    if is_cached(anio, tabla, departamento):
        if progress_cb:
            progress_cb(100)
        return get_cached_paths(anio, tabla, departamento)

    urls = get_parquet_urls(anio, tabla, departamento)
    year_dir = _year_cache_dir(anio)

    # Construir pares (url, dest_path)
    tasks = []
    for url in urls:
        filename = url.split("/")[-1]
        dest = year_dir / filename
        tasks.append((url, dest))

    # Progreso compartido entre hilos
    lock = threading.Lock()
    completed_bytes = [0]
    total_files = len(tasks)
    files_done = [0]

    def download_one(url_dest):
        url, dest = url_dest
        if dest.exists():
            with lock:
                files_done[0] += 1
                if progress_cb:
                    progress_cb(int(files_done[0] / total_files * 100))
            return str(dest)

        def per_file_progress(pct):
            with lock:
                if progress_cb:
                    base = (files_done[0] / total_files) * 100
                    current = (pct / 100) * (1 / total_files) * 100
                    progress_cb(min(99, int(base + current)))

        _download_file(url, dest, per_file_progress)
        with lock:
            files_done[0] += 1
            if progress_cb:
                progress_cb(int(files_done[0] / total_files * 100))
        return str(dest)

    max_workers = min(4, len(tasks))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        paths = list(executor.map(download_one, tasks))

    return paths
