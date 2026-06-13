#!/usr/bin/env python3
"""
Empaqueta el plugin Q-CensosBo y genera el manifiesto del repositorio QGIS.

Produce dos archivos en dist/:
  - qcensosbo.zip   → el plugin listo para instalar (carpeta raíz `qcensosbo/`)
  - plugins.xml     → manifiesto del repositorio de complementos de QGIS

Uso:
    python scripts/build_release.py            # genera ZIP y plugins.xml
    python scripts/build_release.py --zip-only # solo el ZIP (job 'release' del CI)
    python scripts/build_release.py --xml-only # solo plugins.xml (job 'pages' del CI)

El ZIP se publica como asset de GitHub Releases; plugins.xml apunta su
download_url a ese asset. Así la URL del ZIP nunca se cae aunque se redespliegue
el sitio. No requiere dependencias externas (solo la librería estándar).
"""

import argparse
import configparser
import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / "qcensosbo"
DIST = ROOT / "dist"

# El ZIP se sirve como asset del GitHub Release de cada versión (URL estable que
# no depende de Pages). plugins.xml apunta aquí para la auto-actualización de QGIS.
REPO_BASE = "https://github.com/lab-tecnosocial/q-censosbo"


def download_url(version):
    return f"{REPO_BASE}/releases/download/v{version}/qcensosbo.zip"

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
    <download_url>{download_url(g('version'))}</download_url>
  </pyqgis_plugin>
</plugins>
"""
    out = DIST / "plugins.xml"
    out.write_text(xml, encoding="utf-8")
    print(f"✓ {out}  (versión {meta.get('version')})")
    return out


def main():
    parser = argparse.ArgumentParser(description="Empaqueta el plugin Q-CensosBo.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--zip-only", action="store_true", help="genera solo qcensosbo.zip")
    group.add_argument("--xml-only", action="store_true", help="genera solo plugins.xml")
    args = parser.parse_args()

    DIST.mkdir(exist_ok=True)
    if not args.xml_only:
        build_zip()
    if not args.zip_only:
        build_plugins_xml(read_metadata())
    print("Listo. Artefactos en dist/")


if __name__ == "__main__":
    main()
