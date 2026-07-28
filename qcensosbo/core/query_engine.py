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

from . import log
from .data_loader import (
    BASE_URL, TABLE_FILES, DEPT_CODES, release_tag,
)
from .fichas import AREAS

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

NO_UNIDAD_MSG = (
    "El nivel de manzano/comunidad solo existe en las tablas de fichas del "
    "CPV-2024 (no en los microdatos, cuya unidad es la persona o la vivienda)."
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

    Como `os._exit` salta el apagado ordenado de TODO QGIS —incluidos los
    handlers de otros plugins conectados a `aboutToQuit` después de este—, antes
    de salir se vacía la cola de eventos pendientes y se fuerza el volcado de
    QSettings, para no perder los ajustes de la sesión.
    """
    global _hard_exit_registered
    if _hard_exit_registered:
        return
    try:
        from qgis.PyQt.QtCore import QCoreApplication
        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(_hard_exit)
        else:
            raise RuntimeError("QCoreApplication no disponible")
    except Exception:
        import atexit
        atexit.register(os._exit, 0)
    _hard_exit_registered = True


def _hard_exit():
    """Cierra el proceso saltándose los destructores estáticos de DuckDB."""
    try:
        from qgis.PyQt.QtCore import QCoreApplication
        from qgis.core import QgsSettings
        QgsSettings().sync()
        app = QCoreApplication.instance()
        if app is not None:
            app.processEvents()
    except Exception as exc:
        # Estamos saliendo: no hay dónde mostrarlo, pero queda en el log.
        log.aviso("No se pudieron sincronizar los ajustes al cerrar", exc)
    os._exit(0)


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
    except Exception as exc:
        # Sin user-site solo se pierde la instalación con --user.
        log.aviso("No se pudo añadir el directorio user-site", exc)
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
    tag = release_tag(anio, tabla)
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
    if nivel == "unidad":
        return "codigo"
    return "idep" if nivel == "departamento" else "imun"


def _pad_width(nivel):
    """Dígitos del código geográfico. 0 = no rellenar.

    El código de una unidad censal es alfanumérico y no jerárquico
    ("00255016751-A"): no se rellena ni se recorta.
    """
    if nivel == "unidad":
        return 0
    return 2 if nivel == "departamento" else 6


def pad_geo_code(df, nivel):
    """Normaliza la columna geo_code de un DataFrame al ancho del nivel.

    Los códigos de departamento y municipio se comparan como texto con ceros a
    la izquierda (así casan con el GeoJSON), pero DuckDB puede devolverlos como
    número si la columna de origen era entera.
    """
    pad = _pad_width(nivel)
    df["geo_code"] = df["geo_code"].astype(str)
    if pad:
        df["geo_code"] = df["geo_code"].str.zfill(pad)
    return df


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
            # Sin timeout, una red lenta/bloqueada (proxy, firewall, GitHub
            # inaccesible) deja la consulta colgada indefinidamente y la UI se
            # queda en "Cargando variables…". Estos límites hacen que falle de
            # forma visible en vez de colgarse para siempre.
            con.execute("SET http_timeout = 30000;")        # ms por request
            con.execute("SET http_retries = 2;")
            con.execute("SET http_retry_wait_ms = 500;")
        except Exception as exc:
            # Sin httpfs las consultas remotas fallarán con su propio mensaje.
            log.aviso("No se pudo preparar la extensión httpfs de DuckDB", exc)
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
        except Exception as exc:
            log.aviso("No se pudo cerrar la conexión DuckDB", exc)


def _filtered_from(urls, cols, departamento=None, municipio=None, area=None):
    """Fragmento FROM parametrizado, con los filtros geográficos que apliquen.

    DuckDB acepta la lista de rutas/URLs como un único parámetro de
    `read_parquet(?)`, así NO interpolamos rutas en el texto SQL: el driver las
    trata como valor enlazado. Esto evita escapes manuales y los problemas con
    los backslashes de Windows en cualquier plataforma.

    Filtros posibles (todos opcionales, se combinan con AND):
      - `departamento` ("01"…"09") — restringe la agregación y el valor de
        referencia al departamento elegido en vez de a todo el país.
      - `municipio` (código nacional de 6 dígitos) — necesario para el nivel de
        manzano/comunidad, donde el país entero son 268.604 unidades.
      - `area` ("urbana" | "rural") — solo en las tablas de fichas.

    Los códigos se comparan como enteros (TRY_CAST) porque unas tablas los
    guardan con ceros a la izquierda y otras como número.

    Retorna (fragmento_sql, params) para pasar a `con.execute(sql, params)`.
    """
    idep  = _pick_col(cols, _GEO_CANDIDATES["departamento"])
    iprov = _pick_col(cols, _GEO_CANDIDATES["provincia"])
    imun  = _pick_col(cols, _GEO_CANDIDATES["municipio"])

    conds, params = [], []

    if municipio:
        code = str(municipio).zfill(6)
        partes = ((idep, code[:2]), (iprov, code[2:4]), (imun, code[4:6]))
        if not all(col for col, _ in partes):
            raise ValueError(NO_MUNICIPIO_MSG)
        for col, val in partes:
            conds.append(f"TRY_CAST({col} AS INTEGER) = ?")
            params.append(int(val))
    elif departamento and idep:
        try:
            conds.append(f"TRY_CAST({idep} AS INTEGER) = ?")
            params.append(int(str(departamento)))
        except (ValueError, TypeError):
            conds.pop()

    if area:
        col_area = _pick_col(cols, ["area"])
        cod_area = AREAS.get(str(area).lower())
        if col_area and cod_area:
            conds.append(f"TRY_CAST({col_area} AS INTEGER) = ?")
            params.append(cod_area)

    if not conds:
        return "read_parquet(?)", [list(urls)]
    return (f"(SELECT * FROM read_parquet(?) WHERE {' AND '.join(conds)}) AS _src",
            [list(urls)] + params)


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
    except Exception as exc:
        # Sin schema se usan los nombres estándar (_geo_col) como respaldo.
        log.aviso(f"No se pudo leer el schema de {url}", exc)
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


def _build_geo_parts(con, urls, nivel, departamento=None, municipio=None, area=None):
    """
    Retorna (src_clause, geo_select_sql, group_col, params). `src_clause` lleva
    placeholder(s) `?` y `params` los valores correspondientes (ver _filtered_from).

    Camino preferido (todos los censos ya traen geografía en la tabla): construye
    el código a partir de las columnas reales — departamento = idep(2);
    municipio = idep(2)+iprov(2)+imun(2), igual que el GeoJSON; unidad = el
    `codigo` de la ficha, que ya identifica el manzano o la comunidad.

    Fallback: archivos 2024 particionados que aún no tengan columna idep → extrae
    el departamento del nombre de archivo virtual de DuckDB.

    Los filtros (`departamento`, `municipio`, `area`) se aplican vía _filtered_from.
    """
    cols = _describe_cols(con, urls[0])
    idep = _pick_col(cols, _GEO_CANDIDATES["departamento"])

    if nivel == "unidad":
        codigo = _pick_col(cols, ["codigo", "cod_unidad", "id_unidad"])
        if not codigo:
            raise ValueError(NO_UNIDAD_MSG)
        src, params = _filtered_from(urls, cols, departamento, municipio, area)
        return src, f"CAST({codigo} AS VARCHAR) AS geo_code", "geo_code", params

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

    src, params = _filtered_from(urls, cols, departamento, municipio, area)

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
                  category=None, departamento=None, municipio=None, area=None):
    """
    Agrega datos por unidad geográfica con DuckDB. `urls` pueden ser URLs
    remotas o rutas locales: read_parquet acepta ambas indistintamente.

    Maneja tanto archivos históricos (con columna idep/imun) como archivos
    particionados del 2024 (sin columna idep, geo extraído del nombre de archivo).
    `departamento`, `municipio` y `area` restringen el territorio agregado.
    Retorna DataFrame [geo_code, valor].
    """
    con = _make_con(urls)
    src, geo_select, group, params = _build_geo_parts(
        con, urls, nivel, departamento, municipio, area)

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
        # Denominador = casos VÁLIDOS de la variable (COUNT(col) ignora los NULL),
        # no COUNT(*). Muchas preguntas del censo solo aplican a un subconjunto
        # (77 de las 119 columnas de 2024/personas tienen <99 % de cobertura), y
        # dividir por el total de registros subestimaba el porcentaje —hasta 2,5×—
        # además de ser incoherente con el resto de agregaciones, que ya filtran
        # los NULL. Ver `variable_coverage` para informar del universo real.
        sql = f"""
            SELECT {geo_select},
                   ROUND(100.0 * COUNT(CASE WHEN {_cat_filter_sql(variable, category)}
                                            THEN 1 END)
                         / NULLIF(COUNT({variable}), 0), 2) AS valor
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
    return pad_geo_code(df, nivel)


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
        # Mismo denominador que aggregate_geo: casos válidos, no todos los registros.
        return (f"ROUND(100.0 * COUNT(*) FILTER (WHERE {_cat_filter_sql(v, category)}) "
                f"/ NULLIF(COUNT({v}), 0), 2)")
    return "COUNT(*)"


def aggregate_national(urls, variable="__count__", agg="__count__",
                       category=None, departamento=None, municipio=None,
                       area=None):
    """Valor de referencia (un escalar, sin desagregar por geografía) vía DuckDB.

    `urls` pueden ser URLs remotas o rutas locales. Los filtros geográficos
    acotan la referencia al territorio elegido (departamental o municipal en vez
    de nacional)."""
    con = _make_con(urls)
    cols = _describe_cols(con, urls[0])
    src, params = _filtered_from(urls, cols, departamento, municipio, area)
    expr = _national_value_sql(variable, agg, category)
    where = "" if agg in ("__count__", "pct_category") else f" WHERE {variable} IS NOT NULL"
    try:
        row = con.execute(f"SELECT {expr} AS v FROM {src}{where}", params).fetchone()
        return row[0] if row else None
    except Exception:
        return None
    finally:
        _close(con)


def variable_coverage(urls, variable, departamento=None, municipio=None, area=None):
    """(n_total, n_validos) de una variable en el territorio consultado.

    El porcentaje de una categoría se calcula sobre los casos válidos, así que el
    panel necesita decir cuál es ese universo: muchas preguntas del censo solo
    aplican a un subgrupo (mujeres en edad fértil, ocupados, mayores de 4 años…).
    Es un par de COUNT sobre una sola columna: barato incluso en remoto.

    Retorna (None, None) si la consulta falla, para que el resumen simplemente
    omita la nota en vez de romper.
    """
    if variable in (None, "", "__count__", "__loading__", "__error__"):
        return None, None
    con = _make_con(urls)
    try:
        cols = _describe_cols(con, urls[0])
        src, params = _filtered_from(urls, cols, departamento, municipio, area)
        row = con.execute(
            f"SELECT COUNT(*), COUNT({variable}) FROM {src}", params).fetchone()
        return (int(row[0]), int(row[1])) if row else (None, None)
    except Exception:
        return None, None
    finally:
        _close(con)


def distinct_values(urls, variable, limit=60, departamento=None, municipio=None,
                    area=None):
    """Valores distintos de una variable, ordenados, como lista de strings.

    Respaldo para las variables categóricas que el diccionario de etiquetas no
    cubre: sin esto, «Porcentaje» pedía una categoría que la UI no podía ofrecer.
    Se limita a `limit` valores porque un dominio mayor no es categórico en la
    práctica (y el combo sería inservible).
    """
    if variable in (None, "", "__count__", "__loading__", "__error__"):
        return []
    con = _make_con(urls)
    try:
        cols = _describe_cols(con, urls[0])
        src, params = _filtered_from(urls, cols, departamento, municipio, area)
        rows = con.execute(
            f"SELECT DISTINCT CAST({variable} AS VARCHAR) AS v FROM {src} "
            f"WHERE {variable} IS NOT NULL ORDER BY v LIMIT {int(limit) + 1}",
            params).fetchall()
        vals = [str(r[0]) for r in rows]
        return [] if len(vals) > limit else vals
    except Exception:
        return []
    finally:
        _close(con)


def national_custom_sql(urls, sql_expr, departamento=None, municipio=None,
                        area=None):
    """Igual que aggregate_national, pero con una expresión agregada libre.

    Es la referencia de las fichas (cuyo indicador ya viene como expresión) y del
    modo SQL avanzado: el mismo cálculo sin GROUP BY, sobre todo el territorio.
    """
    con = _make_con(urls)
    cols = _describe_cols(con, urls[0])
    src, params = _filtered_from(urls, cols, departamento, municipio, area)
    try:
        row = con.execute(f"SELECT ({sql_expr}) AS v FROM {src}", params).fetchone()
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

def aggregate_custom_sql(urls, nivel, sql_expr, departamento=None,
                         municipio=None, area=None):
    """
    Agrega datos con una expresión SQL libre.

    sql_expr es solo la fórmula para el campo 'valor', por ejemplo:
        "AVG(p26_edad)"
        "100.0 * SUM(CASE WHEN p25_sexo = 1 THEN 1 END) / COUNT(*)"

    La usa tanto el modo SQL avanzado del panel como los indicadores de ficha,
    que se declaran en `fichas.py` justamente como expresión agregada.

    El plugin envuelve la expresión con el GROUP BY geográfico.
    `urls` pueden ser URLs remotas o rutas locales.
    Retorna DataFrame [geo_code, valor].
    """
    con = _make_con(urls)
    src, geo_select, group, params = _build_geo_parts(
        con, urls, nivel, departamento, municipio, area)

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
    return pad_geo_code(df, nivel)


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
    except Exception as exc:
        log.aviso("Falló el recolector de basura durante la limpieza", exc)
