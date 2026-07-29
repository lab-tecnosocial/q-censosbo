"""
URLs de los releases del paquete censosbo y descarga/caché de los diccionarios.

Los datos (parquet grandes) NO se descargan: DuckDB los consulta en remoto sin
bajarlos. Solo los diccionarios (variables y etiquetas, <1 MB) se descargan y
cachean localmente para leerlos rápido.

Estructura de releases:
  - 2024 personas: particionado por departamento (persona_dep01.parquet … persona_dep09.parquet)
  - 2024 otras tablas: un archivo nacional (vivienda.parquet, etc.)
  - Históricos (2012, 2001, 1992, 1976): un archivo nacional por tabla
  - Fichas por manzano/comunidad (2024): release propio, ver `fichas.py`
"""

import os
from pathlib import Path

from . import log
from .fichas import TAG as FICHAS_TAG

BASE_URL = "https://github.com/lab-tecnosocial/censosbo/releases/download"

RELEASES = {
    2024: "data-v1.0.0",
    2012: "data-2012-v1.0.0",
    2001: "data-2001-v1.0.0",
    1992: "data-1992-v1.0.0",
    1976: "data-1976-v1.0.0",
}

# Tablas que NO viven en el release de su año, sino en uno propio.
TABLE_RELEASES = {
    (2024, "unidades"): FICHAS_TAG,
    (2024, "fichas"):   FICHAS_TAG,
}

# Nombre del archivo por (año, tabla). 2024/personas es especial (particionado).
TABLE_FILES = {
    (2024, "personas"):    None,          # especial: persona_dep{dd}.parquet
    (2024, "viviendas"):   "vivienda.parquet",
    (2024, "emigracion"):  "emigracion.parquet",
    (2024, "mortalidad"):  "mortalidad.parquet",
    (2024, "unidades"):    "unidad.parquet",
    (2024, "fichas"):      "ficha.parquet",
    (2012, "personas"):    "persona.parquet",
    (2012, "viviendas"):   "vivienda.parquet",
    (2012, "emigracion"):  "emigracion.parquet",
    # 2012 no tiene mortalidad.parquet en el release
    (2001, "personas"):    "persona.parquet",
    (2001, "viviendas"):   "vivienda.parquet",
    (1992, "personas"):    "persona.parquet",
    (1992, "viviendas"):   "vivienda.parquet",
    (1992, "mortalidad"):  "mortalidad.parquet",
    (1976, "personas"):    "poblacion.parquet",
    (1976, "viviendas"):   "vivienda.parquet",
}

# Archivos de diccionario de variables por año
DICT_FILES = {
    2024: "diccionario_variables.parquet",
    2012: "diccionario_variables.parquet",
    2001: "diccionario_variables.parquet",
    1992: "diccionario_variables.parquet",
    1976: "diccionario_variables.parquet",
}

DEPT_CODES = ["01", "02", "03", "04", "05", "06", "07", "08", "09"]


def release_tag(anio, tabla=None):
    """Tag del release donde vive una tabla.

    Casi todas las tablas están en el release de su año; las fichas por manzano
    y comunidad tienen el suyo (ver TABLE_RELEASES).
    """
    return TABLE_RELEASES.get((anio, tabla)) or RELEASES[anio]


def cache_dir():
    path = Path.home() / ".censosbo_qgis"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _year_cache_dir(anio):
    path = cache_dir() / str(anio)
    path.mkdir(parents=True, exist_ok=True)
    return path


def fichas_cache_dir():
    """Caché de los archivos del release de fichas, separada de los años."""
    path = cache_dir() / "fichas"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _etag_path(dest_path):
    """Sidecar con el validador HTTP del archivo cacheado."""
    return str(dest_path) + ".etag"


def _peticion(url):
    """QNetworkRequest con lo que necesitan los assets de GitHub Releases."""
    from qgis.PyQt.QtCore import QUrl
    from qgis.PyQt.QtNetwork import QNetworkRequest

    peticion = QNetworkRequest(QUrl(url))
    peticion.setRawHeader(b"User-Agent", b"q-censosbo-qgis")
    # Los assets de GitHub Releases redirigen a un CDN, así que hay que seguir la
    # redirección. En Qt5 no es el comportamiento por omisión y hay que pedirlo; en
    # Qt6 (QGIS 4) sí lo es, y el atributo desapareció —estaba obsoleto desde Qt
    # 5.15—, así que ahí no hay nada que activar. `getattr` cubre las dos ramas sin
    # tener que preguntar por la versión.
    seguir = getattr(QNetworkRequest, "FollowRedirectsAttribute", None)
    if seguir is not None:
        peticion.setAttribute(seguir, True)
    return peticion


def _validador_de(respuesta):
    """Validador que identifica la versión del recurso, leído de la respuesta.

    Se prefiere el `ETag`; si el servidor no lo da, vale la combinación de
    `Last-Modified` y `Content-Length`. La misma función se usa al descargar y al
    comprobar, para que las dos cadenas sean comparables.
    """
    def cabecera(nombre):
        return bytes(respuesta.rawHeader(nombre)).decode("utf-8", "ignore").strip()

    etag = cabecera(b"ETag")
    if etag:
        return etag
    respaldo = f"{cabecera(b'Last-Modified')}|{cabecera(b'Content-Length')}"
    return respaldo if respaldo.strip("|") else None


def _validador_remoto(url):
    """Validador actual del recurso, con un HEAD. `None` si no se pudo consultar.

    Se usa **HEAD y no una petición condicional**: `If-None-Match` no sirve aquí
    porque GitHub responde 302 hacia su CDN y Qt **descarta las cabeceras propias al
    redirigir a otro host**, así que el destino final nunca ve el validador y
    contesta 200 con el archivo entero (comprobado). El HEAD, en cambio, sí atraviesa
    la redirección y devuelve las cabeceras del recurso real con **cuerpo vacío**, así
    que comprobar si algo cambió cuesta lo mismo para un diccionario de 15 KB que
    para las geometrías de manzanos.
    """
    from qgis.core import QgsBlockingNetworkRequest
    from qgis.PyQt.QtNetwork import QNetworkRequest

    bloqueante = QgsBlockingNetworkRequest()
    # forceRefresh: sin esto la caché de red de QGIS puede responder sin salir a
    # internet, y entonces la validación no valida nada.
    if bloqueante.head(_peticion(url), True) != \
            QgsBlockingNetworkRequest.ErrorCode.NoError:
        log.aviso(f"No se pudo consultar el estado de {url}: "
                  f"{bloqueante.errorMessage()}")
        return None

    respuesta = bloqueante.reply()
    if respuesta.attribute(
            QNetworkRequest.Attribute.HttpStatusCodeAttribute) != 200:
        return None
    return _validador_de(respuesta)


def _leer_etag(dest_path):
    ruta = _etag_path(dest_path)
    if not os.path.exists(ruta):
        return None
    try:
        with open(ruta, encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError as exc:
        log.aviso(f"No se pudo leer el validador de {dest_path}", exc)
        return None


def _guardar_etag(dest_path, etag):
    if not etag:
        return
    try:
        with open(_etag_path(dest_path), "w", encoding="utf-8") as f:
            f.write(etag)
    except OSError as exc:
        # Sin sidecar la caché sigue sirviendo; solo se revalidará descargando.
        log.aviso(f"No se pudo guardar el validador de {dest_path}", exc)


def _download_file(url, dest_path, progress_cb=None):
    """Descarga un archivo a `dest_path`, **revalidando** la copia en caché.

    Usa **la pila de red de QGIS** (`QgsBlockingNetworkRequest`), no `urllib`, por
    dos razones:

      - Respeta los ajustes de **proxy** y los certificados configurados en QGIS.
        Con `urllib` el plugin no veía el proxy, lo que rompe la descarga en redes
        institucionales (justo el público de este plugin). Es además lo que pide la
        guía de publicación de complementos.
      - `urllib.request.urlopen` atiende también `file:` y `ftp:`, y el escáner de
        seguridad del repositorio oficial lo marca como hallazgo bloqueante
        (Bandit B310).

    **Por qué revalida.** Antes bastaba con que el archivo existiera para darlo por
    bueno. Pero los assets del paquete `censosbo` se **republican sobre el mismo tag
    y el mismo nombre**: el 29/07/2026 los diccionarios pasaron de 4 a 14 columnas
    sin cambiar de URL, y ninguna instalación con caché previa lo habría visto nunca.
    Así que antes de dar la caché por buena se compara su validador con el del
    remoto, con un HEAD (ver `_validador_remoto`, que explica por qué HEAD y no una
    petición condicional).

    Cuatro casos, todos deliberados:

      - **Nada en caché**: descarga y guarda el validador.
      - **Validador guardado igual al remoto**: se usa la copia local, sin descargar.
      - **Distinto, o sin validador guardado** (caché de una versión anterior del
        plugin): descarga. Esto migra solo a quien ya tenía datos viejos, sin pedirle
        vaciar nada a mano.
      - **Fallo de red**: si hay copia local, se usa y se registra el aviso; el
        plugin sigue funcionando sin conexión en vez de romperse.

    Va en un QThread (ver los workers del panel), así que la variante *blocking* es
    la adecuada: no bloquea la interfaz.
    """
    if not str(url).startswith("https://"):
        raise ValueError(f"Solo se descargan URLs https, no: {url}")

    from qgis.core import QgsBlockingNetworkRequest

    en_cache = os.path.exists(dest_path)
    if en_cache:
        guardado = _leer_etag(dest_path)
        remoto = _validador_remoto(url) if guardado else None
        if guardado and remoto and guardado == remoto:
            if progress_cb:
                progress_cb(100)
            return
        if guardado and remoto is None:
            # No se pudo consultar (sin red, o el servidor no colabora): la copia
            # local es mejor que nada.
            log.aviso(f"No se pudo comprobar si {url} cambió; se usa la caché.")
            if progress_cb:
                progress_cb(100)
            return

    bloqueante = QgsBlockingNetworkRequest()
    if progress_cb:
        bloqueante.downloadProgress.connect(
            lambda recibido, total: progress_cb(
                int(recibido / total * 100) if total > 0 else 0))

    codigo = bloqueante.get(_peticion(url), True)

    if codigo != QgsBlockingNetworkRequest.ErrorCode.NoError:
        if en_cache:
            log.aviso(f"No se pudo actualizar {url}; se usa la copia en caché: "
                      f"{bloqueante.errorMessage()}")
            if progress_cb:
                progress_cb(100)
            return
        raise RuntimeError(
            f"No se pudo descargar {url}: {bloqueante.errorMessage()}")

    respuesta = bloqueante.reply()
    contenido = bytes(respuesta.content())
    if not contenido:
        # Sobrescribir una caché buena con un cuerpo vacío sería peor que no tocarla.
        if en_cache:
            log.aviso(f"La descarga de {url} llegó vacía; se usa la copia en caché.")
            if progress_cb:
                progress_cb(100)
            return
        raise RuntimeError(f"La descarga de {url} llegó vacía.")

    # Escritura atómica: un archivo a medias en la caché se daría por bueno en la
    # siguiente ejecución.
    tmp_path = str(dest_path) + ".tmp"
    try:
        with open(tmp_path, "wb") as f:
            f.write(contenido)
        os.replace(tmp_path, dest_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise

    _guardar_etag(dest_path, _validador_de(respuesta))

    if progress_cb:
        progress_cb(100)


def download_codebook(anio, progress_cb=None):
    """
    Descarga el diccionario de variables para el año dado.
    Retorna ruta local (str) o None si no existe.
    """
    filename = DICT_FILES.get(anio)
    if not filename:
        return None

    tag = RELEASES[anio]
    year_dir = _year_cache_dir(anio)
    dest = year_dir / filename
    url = f"{BASE_URL}/{tag}/{filename}"

    try:
        _download_file(url, dest, progress_cb)
        return str(dest)
    except Exception:
        return None


def download_labels_codebook(anio, progress_cb=None):
    """
    Descarga diccionario_etiquetas.parquet para el año dado.
    Contiene el mapeo código → etiqueta para variables categóricas.
    Retorna ruta local (str) o None si no está disponible.
    """
    tag = RELEASES.get(anio)
    if not tag:
        return None
    year_dir = _year_cache_dir(anio)
    dest = year_dir / "diccionario_etiquetas.parquet"
    url = f"{BASE_URL}/{tag}/diccionario_etiquetas.parquet"
    try:
        _download_file(url, dest, progress_cb)
        return str(dest)
    except Exception:
        return None


def download_ficha_file(filename, progress_cb=None):
    """Descarga (y cachea) un archivo del release de fichas. Ruta local o None.

    Se usa para las geometrías de manzanos y comunidades, que hay que leer
    completas para dibujar un municipio.
    """
    dest = fichas_cache_dir() / filename
    url = f"{BASE_URL}/{FICHAS_TAG}/{filename}"
    try:
        _download_file(url, dest, progress_cb)
        return str(dest)
    except Exception:
        return None


def get_tables_for_year(anio):
    """Retorna lista de (etiqueta, clave) de tablas disponibles para el año."""
    available = {
        2024: [("Personas", "personas"), ("Viviendas", "viviendas"),
               ("Emigración", "emigracion"), ("Mortalidad", "mortalidad"),
               ("Ficha de indicadores (manzano/comunidad)", "fichas"),
               ("Unidades censales (manzano/comunidad)", "unidades")],
        2012: [("Personas", "personas"), ("Viviendas", "viviendas"),
               ("Emigración", "emigracion")],
        2001: [("Personas", "personas"), ("Viviendas", "viviendas")],
        1992: [("Personas", "personas"), ("Viviendas", "viviendas"),
               ("Mortalidad", "mortalidad")],
        1976: [("Personas", "personas"), ("Viviendas", "viviendas")],
    }
    return available.get(anio, [])
