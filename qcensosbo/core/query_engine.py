"""
Motor de consulta para archivos parquet del censosbo.

DuckDB es el ÚNICO motor, y lee parquet local o remoto con la misma API
(`read_parquet(...)`): solo cambia la fuente. No se usa pyarrow.

  - Remoto (URL https): consulta directa sin descargar el archivo. Parquet guarda
    estadísticas en el footer; DuckDB hace HTTP range requests y lee solo las
    columnas/rowgroups necesarios. Un COUNT(*) por depto transfiere <2 MB en vez
    de cientos de MB.
  - Local (ruta cacheada): mismo SQL, sin red ni extensión httpfs. DuckDB hace
    projection pushdown y agrega en C++ (mucho más rápido que cargar a pandas).

DuckDB se instala automáticamente en la primera apertura del panel (no requiere
acción del usuario más allá de instalar el plugin).
"""

import os
import re
import subprocess
import sys
import sysconfig

from .data_loader import (
    BASE_URL, RELEASES, TABLE_FILES, DEPT_CODES,
)

# ─────────────────────────────────────────────────────────────────────────────
# DuckDB: detección y auto-instalación
# ─────────────────────────────────────────────────────────────────────────────

_duckdb = None
_duckdb_checked = False
_hard_exit_registered = False
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
        _register_hard_exit()
    except ImportError:
        _duckdb = None
    return _duckdb


def _register_hard_exit():
    """Evita el SIGABRT de DuckDB al cerrar QGIS.

    Una vez cargada, la librería `_duckdb.so` ejecuta destructores estáticos en
    `__cxa_finalize` (al hacer `exit()`) que invocan `PyEval_SaveThread` cuando el
    intérprete de Python YA finalizó → `abort()`. No hay forma de evitarlo desde
    Python cerrando conexiones (todas se cierran y aun así ocurre). La salida
    fiable es terminar el proceso con `os._exit()` al recibir `aboutToQuit` de Qt:
    se ejecuta antes de que el proceso entre en `exit()` y dispare esos
    destructores estáticos, saltándoselos.

    Solo se registra si DuckDB llegó a importarse (las sesiones que no lo usan no
    se ven afectadas). No interfiere con recargar el plugin: `aboutToQuit` solo
    dispara al cerrar la aplicación, no en `unload()`.
    """
    global _hard_exit_registered
    if _hard_exit_registered:
        return
    try:
        from qgis.PyQt.QtCore import QCoreApplication
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(lambda: os._exit(0))
        else:
            raise RuntimeError("QCoreApplication no disponible")
    except Exception:
        import atexit
        atexit.register(os._exit, 0)
    _hard_exit_registered = True


def duckdb_available():
    return _try_duckdb() is not None


def _python_executable():
    """Ruta al intérprete Python real de QGIS.

    `sys.executable` suele apuntar al binario de la app, no a un python invocable
    con `-m pip`: en Windows es `qgis-bin.exe`; en macOS es `.../Contents/MacOS/QGIS`.

    Estrategia: `sysconfig` conoce de forma autoritativa el directorio de binarios
    del intérprete que ESTÁ ejecutando QGIS (su python embebido, no el del sistema);
    lo usamos como fuente principal. Como respaldo para layouts atípicos, buscamos
    el ejecutable —incluyendo nombres versionados (`python3.12`)— en el directorio
    del propio ejecutable y en los prefijos. Si nada aparece, devuelve None (el
    caller avisa para reintentar; NUNCA caemos al binario de la app, que relanzaría
    otra instancia de QGIS).
    """
    exe = sys.executable or ""
    if os.path.basename(exe).lower().startswith("python"):
        return exe

    if os.name == "nt":
        rx = re.compile(r"^python(\d+(\.\d+)*)?\.exe$", re.IGNORECASE)
        subdirs = ("", "Scripts", "bin")
    else:
        rx = re.compile(r"^python(\d+(\.\d+)*)?$")
        subdirs = ("", "bin")

    # Fuente autoritativa primero (sysconfig), luego respaldos por ubicación.
    # En Windows el python.exe vive en el PADRE del dir "scripts"
    # (…/Python312/python.exe junto a …/Python312/Scripts), así que incluimos
    # también ese padre. En macOS el ejecutable está junto al binario de la app.
    scripts = sysconfig.get_path("scripts")
    bases = [
        scripts,
        os.path.dirname(scripts) if scripts else None,
        sysconfig.get_config_var("BINDIR"),
        os.path.dirname(exe),
        sys.prefix, sys.exec_prefix, sys.base_prefix,
    ]
    for base in bases:
        if not base:
            continue
        for sub in subdirs:
            d = os.path.join(base, sub) if sub else base
            try:
                names = sorted(os.listdir(d))
            except OSError:
                continue
            for name in names:
                if rx.match(name):
                    cand = os.path.join(d, name)
                    if os.path.isfile(cand) and os.access(cand, os.X_OK):
                        return cand
    return None


def _run_pip(python, extra_args, timeout=180):
    """Ejecuta `python -m pip install ...` sin abrir consola en Windows.

    Retorna (returncode, salida_combinada)."""
    flags = 0
    if os.name == "nt":
        flags = 0x08000000  # CREATE_NO_WINDOW: evita el parpadeo de una consola
    proc = subprocess.run(
        [python, "-m", "pip", "install", "duckdb",
         "--disable-pip-version-check", *extra_args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        creationflags=flags,
        text=True,
    )
    return proc.returncode, (proc.stdout or "").strip()


def _reimport_duckdb():
    """Fuerza re-importación de duckdb (incluye user-site por si se usó --user)."""
    global _duckdb, _duckdb_checked
    try:
        import site
        site.addsitedir(site.getusersitepackages())
    except Exception:
        pass
    _duckdb_checked = False
    _duckdb = None
    return _try_duckdb() is not None


def install_duckdb(status_cb=None, done_cb=None):
    """
    Instala duckdb en el Python de QGIS. Llamar en un QThread (no bloquea la UI).

    Estrategia robusta para Windows/instalaciones sin permisos:
      1. Localiza el python real de QGIS (no `qgis-bin.exe`).
      2. Intenta `pip install` normal.
      3. Si falla (p. ej. QGIS en Program Files sin admin), reintenta con
         `--user` (instala en el perfil del usuario, sin permisos elevados).

    status_cb(str):        callback con texto de estado
    done_cb(bool, str):    callback al terminar — (éxito, mensaje accionable)
    """
    # Ya está instalado
    if _try_duckdb():
        if done_cb:
            done_cb(True, "DuckDB ya estaba disponible.")
        return

    if status_cb:
        status_cb("Instalando DuckDB (solo la primera vez)…")

    python = _python_executable()
    # El flujo normal instala con el Python de QGIS detectado. Solo si NO se pudo
    # ubicar (None) evitamos ejecutar pip: hacerlo contra el binario de la app
    # relanzaría otra instancia de QGIS. En ese caso avisamos para reintentar
    # (reabrir el panel vuelve a llamar a install_duckdb desde showEvent).
    if not python:
        msg = ("No se pudo ubicar el Python de QGIS para instalar DuckDB. "
               "Reabre el panel para reintentar.")
        if status_cb:
            status_cb(msg)
        if done_cb:
            done_cb(False, msg)
        return

    attempts = [
        ([], "Instalando DuckDB (solo la primera vez)…"),
        (["--user"], "Reintentando en tu perfil de usuario…"),
    ]

    last_output = ""
    for extra, msg in attempts:
        if status_cb:
            status_cb(msg)
        try:
            code, out = _run_pip(python, extra)
            last_output = out
            if code == 0 and _reimport_duckdb():
                if status_cb:
                    status_cb("DuckDB instalado correctamente.")
                if done_cb:
                    done_cb(True, "DuckDB instalado correctamente.")
                return
        except subprocess.TimeoutExpired:
            last_output = "La instalación superó el tiempo de espera."
        except Exception as exc:
            last_output = str(exc)

    # Fallo en ambos intentos: mensaje accionable (última línea útil de pip)
    detalle = last_output.splitlines()[-1] if last_output else ""
    mensaje = ("No se pudo instalar DuckDB. Revisa tu conexión a internet "
               "y los permisos." + (f" Detalle: {detalle}" if detalle else ""))
    if status_cb:
        status_cb(mensaje)
    if done_cb:
        done_cb(False, mensaje)


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

def get_columns(source):
    """
    Lee el schema de un parquet (local o remoto) con DuckDB — solo el footer.
    Retorna lista de nombres de columnas o [] si falla.
    """
    if not duckdb_available():
        return []
    con = None
    try:
        con = _make_con([source])
        result = con.execute(
            "DESCRIBE SELECT * FROM read_parquet(?) LIMIT 0", [source]
        ).fetchall()
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


def _is_remote(srcs):
    """True si alguna fuente es una URL remota (http/https)."""
    return any(str(s).startswith("http") for s in srcs)


def _make_con(srcs=None):
    """Conexión DuckDB.

    Carga la extensión httpfs solo si hay fuentes remotas; para parquet locales
    no se necesita red ni httpfs (así una consulta sobre datos cacheados funciona
    sin internet aunque httpfs nunca se haya instalado).
    """
    duckdb = _try_duckdb()
    if not duckdb:
        raise RuntimeError("DuckDB no disponible.")
    con = duckdb.connect()
    if srcs is None or _is_remote(srcs):
        try:
            con.execute("INSTALL httpfs; LOAD httpfs;")
        except Exception:
            pass
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


def _filtered_from(urls, idep=None, departamento=None):
    """Fragmento FROM parametrizado, opcionalmente filtrado por departamento.

    DuckDB acepta la lista de rutas/URLs como un único parámetro de
    `read_parquet(?)`, así NO interpolamos rutas en el texto SQL: el driver las
    trata como valor enlazado. Esto evita escapes manuales y los problemas con
    los backslashes de Windows en cualquier plataforma.

    Si se pide un `departamento` y el archivo tiene columna `idep` (no está ya
    particionado por depto), envuelve la fuente en una subconsulta que filtra por
    ese departamento. Así, a nivel municipal, la agregación y el valor de
    referencia se restringen al departamento elegido en vez de a todo el país.

    Retorna (fragmento_sql, params) para pasar a `con.execute(sql, params)`.
    """
    if departamento and idep:
        try:
            dep_int = int(str(departamento))
        except (ValueError, TypeError):
            dep_int = None
        if dep_int is not None:
            return (f"(SELECT * FROM read_parquet(?) "
                    f"WHERE TRY_CAST({idep} AS INTEGER) = ?) AS _src",
                    [list(urls), dep_int])
    return "read_parquet(?)", [list(urls)]


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
            "DESCRIBE SELECT * FROM read_parquet(?) LIMIT 0", [url]
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


def _build_geo_parts(con, urls, nivel, departamento=None):
    """
    Retorna (src_clause, geo_select_sql, group_col, params). `src_clause` lleva
    placeholder(s) `?` y `params` los valores correspondientes (ver _filtered_from).

    Camino preferido (todos los censos ya traen geografía en la tabla): construye
    el código a partir de las columnas reales — departamento = idep(2);
    municipio = idep(2)+iprov(2)+imun(2), igual que el GeoJSON.

    Fallback: archivos 2024 particionados que aún no tengan columna idep → extrae
    el departamento del nombre de archivo virtual de DuckDB.

    Si se pide `departamento` (solo aplica a nivel municipal), la fuente se filtra
    a ese departamento vía _filtered_from.
    """
    cols = _describe_cols(con, urls[0])
    idep = _pick_col(cols, _GEO_CANDIDATES["departamento"])

    # Fallback para archivos particionados sin columna idep (ya vienen filtrados
    # por departamento desde get_parquet_urls, así que no re-filtramos aquí).
    if not idep and _is_dept_partitioned(urls):
        src = "read_parquet(?, filename=true)"
        params = [list(urls)]
        dep = "LPAD(regexp_extract(filename, 'persona_dep(\\d+)', 1), 2, '0')"
        if nivel == "departamento":
            return src, f"{dep} AS geo_code", "geo_code", params
        iprov = _pick_col(cols, _GEO_CANDIDATES["provincia"]) or "iprov"
        imun  = _pick_col(cols, _GEO_CANDIDATES["municipio"]) or "imun"
        geo_select = (f"CONCAT({dep}, LPAD(CAST({iprov} AS VARCHAR), 2, '0'), "
                      f"LPAD(CAST({imun} AS VARCHAR), 2, '0')) AS geo_code")
        return src, geo_select, "geo_code", params

    src, params = _filtered_from(urls, idep, departamento)

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
    return src, geo_select, "geo_code", params


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


def aggregate_geo(urls, nivel, variable="__count__", agg="__count__",
                  category=None, departamento=None):
    """
    Agrega datos por unidad geográfica con DuckDB. `urls` pueden ser URLs
    remotas o rutas locales: read_parquet acepta ambas indistintamente.

    Maneja tanto archivos históricos (con columna idep/imun) como archivos
    particionados del 2024 (sin columna idep, geo extraído del nombre de archivo).
    Si se pasa `departamento` (nivel municipal), restringe la agregación a ese
    departamento. Retorna DataFrame [geo_code, valor].
    """
    con = _make_con(urls)
    src, geo_select, group, params = _build_geo_parts(con, urls, nivel, departamento)

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
        df = con.execute(sql, params).df()
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


def aggregate_national(urls, variable="__count__", agg="__count__",
                       category=None, departamento=None):
    """Valor de referencia (un escalar, sin desagregar por geografía) vía DuckDB.

    `urls` pueden ser URLs remotas o rutas locales. Si se pasa `departamento`,
    el escalar se calcula solo sobre ese departamento (referencia departamental
    en vez de nacional)."""
    con = _make_con(urls)
    idep = _pick_col(_describe_cols(con, urls[0]), _GEO_CANDIDATES["departamento"])
    src, params = _filtered_from(urls, idep, departamento)
    expr = _national_value_sql(variable, agg, category)
    where = "" if agg in ("__count__", "pct_category") else f" WHERE {variable} IS NOT NULL"
    try:
        row = con.execute(f"SELECT {expr} AS v FROM {src}{where}", params).fetchone()
        return row[0] if row else None
    except Exception:
        return None
    finally:
        _close(con)


def read_parquet_local_df(path, columns=None):
    """Lee un parquet LOCAL a un DataFrame pandas con DuckDB (sin red/httpfs).

    Usado para los diccionarios (archivos pequeños). `columns` opcional limita
    la proyección. Retorna None si DuckDB no está disponible o falla la lectura.
    """
    if not duckdb_available():
        return None
    con = None
    try:
        con = _make_con([path])
        cols = ", ".join(columns) if columns else "*"
        return con.execute(
            f"SELECT {cols} FROM read_parquet(?)", [path]
        ).df()
    except Exception:
        return None
    finally:
        _close(con)


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

def aggregate_custom_sql(urls, nivel, sql_expr, departamento=None):
    """
    Agrega datos con una expresión SQL libre del usuario.

    sql_expr es solo la fórmula para el campo 'valor', por ejemplo:
        "AVG(p26_edad)"
        "100.0 * SUM(CASE WHEN p25_sexo = 1 THEN 1 END) / COUNT(*)"

    El plugin envuelve la expresión con el GROUP BY geográfico.
    `urls` pueden ser URLs remotas o rutas locales.
    Retorna DataFrame [geo_code, valor].
    """
    con = _make_con(urls)
    src, geo_select, group, params = _build_geo_parts(con, urls, nivel, departamento)

    sql = f"""
        SELECT {geo_select},
               ({sql_expr}) AS valor
        FROM {src}
        GROUP BY {group}
    """
    try:
        df = con.execute(sql, params).df()
    finally:
        _close(con)
    df["geo_code"] = df["geo_code"].astype(str).str.zfill(_pad_width(nivel))
    return df


def cleanup():
    """
    Limpieza para la RECARGA del plugin (se llama desde `plugin.unload()`):
    vacía cachés en memoria y fuerza un GC. No intenta resolver el crash al
    CERRAR QGIS (ese es un destructor estático de la `.so` y lo maneja
    `_register_hard_exit()` vía `aboutToQuit` de Qt).
    """
    _schema_cache.clear()
    try:
        import gc
        gc.collect()
    except Exception:
        pass
