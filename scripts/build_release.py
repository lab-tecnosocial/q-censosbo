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

# Archivos de la raíz del repo que se copian DENTRO de qcensosbo/ en el ZIP.
# El repositorio oficial de complementos RECHAZA el paquete si no encuentra
# LICENSE ("Cannot find LICENSE in the plugin package. This file is required"),
# y su guía de revisión pide también un README. Viven en la raíz del repo, así
# que hay que añadirlos aquí en vez de duplicarlos dentro de qcensosbo/.
EXTRA_ROOT_FILES = ["LICENSE", "README.md"]


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

        for nombre in EXTRA_ROOT_FILES:
            origen = ROOT / nombre
            if not origen.exists():
                raise SystemExit(
                    f"✗ Falta {nombre} en la raíz del repo: el repositorio de "
                    "complementos de QGIS lo exige dentro del paquete.")
            zf.write(origen, f"{PLUGIN_DIR.name}/{nombre}")
            n += 1

    verificar(zip_path)
    print(f"✓ {zip_path}  ({n} archivos)")
    return zip_path


def verificar(zip_path):
    """Comprueba lo que el repositorio oficial valida al subir el paquete."""
    with zipfile.ZipFile(zip_path) as zf:
        nombres = zf.namelist()
    raices = {n.split("/")[0] for n in nombres}
    problemas = []
    if raices != {PLUGIN_DIR.name}:
        problemas.append(f"la raíz del ZIP debe ser solo '{PLUGIN_DIR.name}/': {raices}")
    # `.bandit` es un dotfile: la guía del repositorio oficial advierte de que
    # algunas herramientas de empaquetado los excluyen sin avisar, y sin él el
    # escaneo vuelve a bloquear el plugin por los 18 hallazgos irreducibles.
    for obligatorio in ("metadata.txt", "__init__.py", "LICENSE", ".bandit"):
        if f"{PLUGIN_DIR.name}/{obligatorio}" not in nombres:
            problemas.append(f"falta {obligatorio}")
    basura = [n for n in nombres
              if "__pycache__" in n or n.endswith(".pyc") or ".DS_Store" in n]
    if basura:
        problemas.append(f"archivos que no deben ir: {basura[:3]}")
    if problemas:
        raise SystemExit("✗ ZIP inválido:\n  - " + "\n  - ".join(problemas))


def main():
    build_zip()
    print("Listo. Artefacto en dist/")


if __name__ == "__main__":
    main()
