#!/usr/bin/env python3
"""
Publica una nueva versión de Q-CensosBo.

Uso:
    python scripts/release.py          # sube el patch: 0.1.0 → 0.1.1
    python scripts/release.py minor    # sube el minor: 0.1.0 → 0.2.0
    python scripts/release.py major    # sube el major: 0.1.0 → 1.0.0
    python scripts/release.py 0.3.0    # versión exacta

Pasos que ejecuta:
    1. Verifica que no hay cambios sin commit (árbol limpio).
    2. Compila todos los .py del plugin (detecta errores de sintaxis).
    3. Actualiza version= en qcensosbo/metadata.txt.
    4. git add metadata.txt → git commit → git tag vX.Y.Z → git push + tags.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
METADATA = ROOT / "qcensosbo" / "metadata.txt"


# ── helpers ──────────────────────────────────────────────────────────────────

def run(cmd, check=True):
    return subprocess.run(cmd, shell=True, check=check, capture_output=True, text=True)


def abort(msg):
    print(f"✗ {msg}", file=sys.stderr)
    sys.exit(1)


# ── leer / escribir versión ───────────────────────────────────────────────────

def read_version():
    text = METADATA.read_text(encoding="utf-8")
    m = re.search(r"^version\s*=\s*(.+)$", text, re.MULTILINE)
    if not m:
        abort("No se encontró 'version=' en metadata.txt")
    return m.group(1).strip()


def bump(version, part):
    major, minor, patch = map(int, version.split("."))
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def write_version(new_ver):
    text = METADATA.read_text(encoding="utf-8")
    text = re.sub(r"^(version\s*=\s*).+$", rf"\g<1>{new_ver}", text, flags=re.MULTILINE)
    METADATA.write_text(text, encoding="utf-8")


# ── verificaciones ────────────────────────────────────────────────────────────

def check_clean_tree():
    result = run("git status --porcelain", check=False)
    if result.stdout.strip():
        abort(
            "Hay cambios sin commit. Haz commit o stash antes de publicar:\n"
            + result.stdout.rstrip()
        )


def check_syntax():
    py_files = [
        *ROOT.glob("qcensosbo/*.py"),
        *ROOT.glob("qcensosbo/core/*.py"),
        *ROOT.glob("qcensosbo/panel/*.py"),
        *ROOT.glob("qcensosbo/processing/*.py"),
        *ROOT.glob("qcensosbo/processing/algorithms/*.py"),
    ]
    files_str = " ".join(f'"{p}"' for p in py_files)
    result = run(f'"{sys.executable}" -m py_compile {files_str}', check=False)
    if result.returncode != 0:
        abort(f"Error de sintaxis:\n{result.stderr.rstrip()}")


# ── main ──────────────────────────────────────────────────────────────────────

def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else "patch"

    current = read_version()

    if arg in ("patch", "minor", "major"):
        new_ver = bump(current, arg)
    elif re.fullmatch(r"\d+\.\d+\.\d+", arg):
        new_ver = arg
    else:
        abort(f"Argumento no válido: '{arg}'. Usa patch/minor/major o X.Y.Z")

    print(f"Versión actual : {current}")
    print(f"Nueva versión  : {new_ver}")
    confirm = input("¿Continuar? [s/N] ").strip().lower()
    if confirm not in ("s", "si", "sí", "y", "yes"):
        print("Cancelado.")
        sys.exit(0)

    print("\n1/5 Verificando árbol limpio…")
    check_clean_tree()

    print("2/5 Verificando sintaxis…")
    check_syntax()

    print("3/5 Actualizando metadata.txt…")
    write_version(new_ver)

    print("4/5 Commit y tag…")
    run(f'git add "{METADATA}"')
    run(f'git commit -m "release v{new_ver}"')
    run(f"git tag v{new_ver}")

    print("5/5 Push a origin…")
    run("git push origin main --tags")

    print(f"\n✓ v{new_ver} publicada. El CI construye el ZIP y el sitio.")
    print(f"  https://github.com/lab-tecnosocial/q-censosbo/actions")


if __name__ == "__main__":
    main()
