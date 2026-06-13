#!/usr/bin/env python3
"""
Empaqueta el plugin Q-CensosBo y genera el manifiesto del repositorio QGIS.

Produce dos archivos en dist/:
  - qcensosbo.zip   → el plugin listo para instalar (carpeta raíz `qcensosbo/`)
  - plugins.xml     → manifiesto del repositorio de complementos de QGIS

Uso:
    python scripts/build_release.py

No requiere dependencias externas (solo la librería estándar). Lo usa también
el workflow de GitHub Actions; reutilizable en local para probar el empaquetado.
"""

import configparser
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / "qcensosbo"
DIST = ROOT / "dist"

# URL pública del ZIP en GitHub Pages (para auto-actualización desde QGIS)
PAGES_BASE = "https://lab-tecnosocial.github.io/q-censosbo"
DOWNLOAD_URL = f"{PAGES_BASE}/qcensosbo.zip"

EXCLUDE_DIRS = {"__pycache__"}
EXCLUDE_NAMES = {".DS_Store"}
EXCLUDE_SUFFIXES = {".pyc"}


def read_metadata():
    cfg = configparser.ConfigParser()
    cfg.read(PLUGIN_DIR / "metadata.txt", encoding="utf-8")
    return cfg["general"]


def build_zip():
    DIST.mkdir(exist_ok=True)
    zip_path = DIST / "qcensosbo.zip"
    n = 0
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(PLUGIN_DIR.rglob("*")):
            if path.is_dir():
                continue
            if any(part in EXCLUDE_DIRS for part in path.parts):
                continue
            if path.name in EXCLUDE_NAMES or path.suffix in EXCLUDE_SUFFIXES:
                continue
            # arcname conserva el prefijo "qcensosbo/" (directorio raíz del zip)
            arcname = path.relative_to(ROOT).as_posix()
            zf.write(path, arcname)
            n += 1
    print(f"✓ {zip_path}  ({n} archivos)")
    return zip_path


def build_plugins_xml(meta):
    def g(key, default=""):
        return escape(str(meta.get(key, default)))

    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<plugins>
  <pyqgis_plugin name="{g('name')}" version="{g('version')}">
    <description>{g('description')}</description>
    <about>{g('about')}</about>
    <version>{g('version')}</version>
    <qgis_minimum_version>{g('qgisMinimumVersion', '3.28')}</qgis_minimum_version>
    <homepage>{g('homepage')}</homepage>
    <repository>{g('repository')}</repository>
    <tracker>{g('tracker')}</tracker>
    <author_name>{g('author')}</author_name>
    <tags>{g('tags')}</tags>
    <experimental>{g('experimental', 'False')}</experimental>
    <deprecated>{g('deprecated', 'False')}</deprecated>
    <file_name>qcensosbo.zip</file_name>
    <download_url>{DOWNLOAD_URL}</download_url>
  </pyqgis_plugin>
</plugins>
"""
    out = DIST / "plugins.xml"
    out.write_text(xml, encoding="utf-8")
    print(f"✓ {out}  (versión {meta.get('version')})")
    return out


def main():
    meta = read_metadata()
    build_zip()
    build_plugins_xml(meta)
    print("Listo. Artefactos en dist/")


if __name__ == "__main__":
    main()
