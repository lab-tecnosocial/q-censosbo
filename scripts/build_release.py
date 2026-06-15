#!/usr/bin/env python3
"""
Empaqueta el plugin Q-CensosBo para distribución.

Produce en dist/:
  - qcensosbo.zip   → el plugin listo para instalar (carpeta raíz `qcensosbo/`)

El ZIP se publica como asset de GitHub Releases; la instalación es por
*Complementos → Instalar a partir de ZIP*. No requiere dependencias externas
(solo la librería estándar).

Uso:
    python scripts/build_release.py
"""

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = ROOT / "qcensosbo"
DIST = ROOT / "dist"

EXCLUDE_DIRS = {"__pycache__"}
EXCLUDE_NAMES = {".DS_Store"}
EXCLUDE_SUFFIXES = {".pyc"}


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


def main():
    build_zip()
    print("Listo. Artefacto en dist/")


if __name__ == "__main__":
    main()
