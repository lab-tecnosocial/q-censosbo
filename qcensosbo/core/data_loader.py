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


def _download_file(url, dest_path, progress_cb=None):
    """Descarga un archivo a `dest_path`. Salta si ya existe en caché.

    Usa **la pila de red de QGIS** (`QgsBlockingNetworkRequest`), no `urllib`, por
    dos razones:

      - Respeta los ajustes de **proxy** y los certificados configurados en QGIS.
        Con `urllib` el plugin no veía el proxy, lo que rompe la descarga en redes
        institucionales (justo el público de este plugin). Es además lo que pide la
        guía de publicación de complementos.
      - `urllib.request.urlopen` atiende también `file:` y `ftp:`, y el escáner de
        seguridad del repositorio oficial lo marca como hallazgo bloqueante
        (Bandit B310).

    Va en un QThread (ver los workers del panel), así que la variante *blocking* es
    la adecuada: no bloquea la interfaz.
    """
    if os.path.exists(dest_path):
        if progress_cb:
            progress_cb(100)
        return

    if not str(url).startswith("https://"):
        raise ValueError(f"Solo se descargan URLs https, no: {url}")

    from qgis.core import QgsBlockingNetworkRequest
    from qgis.PyQt.QtCore import QUrl
    from qgis.PyQt.QtNetwork import QNetworkRequest

    peticion = QNetworkRequest(QUrl(url))
    peticion.setRawHeader(b"User-Agent", b"q-censosbo-qgis")
    # Sigue las redirecciones: los assets de GitHub Releases redirigen a un CDN.
    peticion.setAttribute(QNetworkRequest.FollowRedirectsAttribute, True)

    bloqueante = QgsBlockingNetworkRequest()
    if progress_cb:
        bloqueante.downloadProgress.connect(
            lambda recibido, total: progress_cb(
                int(recibido / total * 100) if total > 0 else 0))

    codigo = bloqueante.get(peticion)
    if codigo != QgsBlockingNetworkRequest.NoError:
        raise RuntimeError(
            f"No se pudo descargar {url}: {bloqueante.errorMessage()}")

    contenido = bytes(bloqueante.reply().content())
    if not contenido:
        raise RuntimeError(f"La descarga de {url} llegó vacía.")

    # Escritura atómica: un archivo a medias en la caché se daría por bueno en la
    # siguiente ejecución (el chequeo de caché es "existe el archivo").
    tmp_path = str(dest_path) + ".tmp"
    try:
        with open(tmp_path, "wb") as f:
            f.write(contenido)
        os.replace(tmp_path, dest_path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
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
