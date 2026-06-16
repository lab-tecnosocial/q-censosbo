"""
URLs de los releases del paquete censosbo y descarga/caché de los diccionarios.

Los datos (parquet grandes) NO se descargan: DuckDB los consulta en remoto sin
bajarlos. Solo los diccionarios (variables y etiquetas, <1 MB) se descargan y
cachean localmente para leerlos rápido.

Estructura de releases:
  - 2024 personas: particionado por departamento (persona_dep01.parquet … persona_dep09.parquet)
  - 2024 otras tablas: un archivo nacional (vivienda.parquet, etc.)
  - Históricos (2012, 2001, 1992, 1976): un archivo nacional por tabla
"""

import os
import urllib.request
from pathlib import Path

BASE_URL = "https://github.com/lab-tecnosocial/censosbo/releases/download"

RELEASES = {
    2024: "data-v1.0.0",
    2012: "data-2012-v1.0.0",
    2001: "data-2001-v1.0.0",
    1992: "data-1992-v1.0.0",
    1976: "data-1976-v1.0.0",
}

# Nombre del archivo por (año, tabla). 2024/personas es especial (particionado).
TABLE_FILES = {
    (2024, "personas"):    None,          # especial: persona_dep{dd}.parquet
    (2024, "viviendas"):   "vivienda.parquet",
    (2024, "emigracion"):  "emigracion.parquet",
    (2024, "mortalidad"):  "mortalidad.parquet",
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


def cache_dir():
    path = Path.home() / ".censosbo_qgis"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _year_cache_dir(anio):
    path = cache_dir() / str(anio)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _download_file(url, dest_path, progress_cb=None):
    """Descarga un archivo con progreso. Salta si ya existe en caché."""
    if os.path.exists(dest_path):
        if progress_cb:
            progress_cb(100)
        return

    tmp_path = str(dest_path) + ".tmp"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "q-censosbo-qgis/0.1"})
        with urllib.request.urlopen(req) as response:
            total = int(response.headers.get("Content-Length", 0))
            downloaded = 0
            chunk = 8192
            with open(tmp_path, "wb") as f:
                while True:
                    data = response.read(chunk)
                    if not data:
                        break
                    f.write(data)
                    downloaded += len(data)
                    if progress_cb and total > 0:
                        progress_cb(int(downloaded / total * 100))
        os.rename(tmp_path, dest_path)
        if progress_cb:
            progress_cb(100)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


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


def get_tables_for_year(anio):
    """Retorna lista de (etiqueta, clave) de tablas disponibles para el año."""
    available = {
        2024: [("Personas", "personas"), ("Viviendas", "viviendas"),
               ("Emigración", "emigracion"), ("Mortalidad", "mortalidad")],
        2012: [("Personas", "personas"), ("Viviendas", "viviendas"),
               ("Emigración", "emigracion")],
        2001: [("Personas", "personas"), ("Viviendas", "viviendas")],
        1992: [("Personas", "personas"), ("Viviendas", "viviendas"),
               ("Mortalidad", "mortalidad")],
        1976: [("Personas", "personas"), ("Viviendas", "viviendas")],
    }
    return available.get(anio, [])
