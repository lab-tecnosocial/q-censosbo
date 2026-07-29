#!/usr/bin/env bash
# Prueba el plugin en los EXTREMOS del rango de versiones que declara.
#
# metadata.txt dice `qgisMinimumVersion=3.28` y `qgisMaximumVersion=4.99`, así que
# el plugin tiene que funcionar en las dos ramas de Qt. En macOS solo se puede
# probar el QGIS instalado, y además allí DuckDB no se puede importar desde el
# Python de QGIS (el `.so` está firmado para el bundle: *different Team IDs*), con
# lo que el camino real del motor quedaba sin ejercitar. Los contenedores resuelven
# las dos cosas:
#
#   min    QGIS 3.28 · Qt 5.15 · pandas 2 → la rama QVariant de core/compat.py
#   qgis4  QGIS 4.2  · Qt 6.10 · PyQt6    → la rama QMetaType y todo el código Qt6
#   e2e    DuckDB de verdad dentro de QGIS, contrastado con el fixture de Qt5
#
# El QGIS intermedio (3.38-3.44) se prueba en local con el bundle de macOS; el
# comando está en dev-docs/README.md.
#
# Uso:
#   scripts/qa_versiones.sh check    # incompatibilidades Qt6 estáticas
#   scripts/qa_versiones.sh min      # suite en el mínimo declarado
#   scripts/qa_versiones.sh qgis4    # suite en QGIS 4
#   scripts/qa_versiones.sh e2e      # end-to-end real (necesita internet)
#   scripts/qa_versiones.sh instalacion  # el ZIP publicable instala y arranca
#   scripts/qa_versiones.sh todo     # todas
#
# Requiere Docker. En Apple Silicon las imágenes son solo amd64, así que corren
# emuladas: funcionan, pero van lentas.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CHECKER="ghcr.io/qgis/pyqgis4-checker:main-ubuntu"
PLATAFORMA="linux/amd64"

cd "$ROOT"

construir() {  # construir <etiqueta> <dockerfile>
  echo "▸ Construyendo $1 (la primera vez baja varios GB)…"
  docker build --platform "$PLATAFORMA" -t "$1" -f "scripts/qa_docker/$2" scripts/qa_docker
}

correr() {  # correr <etiqueta> <comando...>
  local imagen="$1"; shift
  # Sin -e HOME: pandas y duckdb viven en el home del usuario de la imagen, y
  # sobrescribirlo los saca del sys.path.
  docker run --rm --platform "$PLATAFORMA" \
    -v "$ROOT:/workspace" --workdir /workspace \
    -e QT_QPA_PLATFORM=offscreen \
    "$imagen" "$@"
}

exige_fixture() {
  if [ ! -f dist/qa_fixture.pkl ]; then
    echo "✗ falta dist/qa_fixture.pkl. Genéralo con:" >&2
    echo "    uv run --with duckdb --with pandas python scripts/qa_fixture.py" >&2
    exit 1
  fi
}

tarea_check() {
  echo "▸ pyqgis4-checker (dry run) sobre qcensosbo/"
  mkdir -p dist
  docker run --rm --pull always --platform "$PLATAFORMA" \
    --user "$(id -u):$(id -g)" \
    -v "$ROOT:/workspace" --workdir /workspace \
    "$CHECKER" \
    pyqt5_to_pyqt6.py --dry_run --logfile /workspace/dist/pyqt6_checker.log qcensosbo
  # El checker sale con 0 aunque encuentre cosas, así que hay que mirar el log:
  # una sola línea es solo la cabecera «Start Logs», o sea cero hallazgos.
  local n
  n=$(($(wc -l < dist/pyqt6_checker.log) - 1))
  if [ "$n" -gt 0 ]; then
    echo "✗ $n incompatibilidades Qt6:"; tail -n +2 dist/pyqt6_checker.log; return 1
  fi
  echo "✓ sin incompatibilidades Qt6"
}

tarea_min() {
  exige_fixture
  construir qcensosbo-qa:qgis328 Dockerfile.qgis328
  echo "▸ Suite en QGIS 3.28 (mínimo declarado, rama QVariant)"
  correr qcensosbo-qa:qgis328 python3 scripts/qa_headless.py
}

tarea_qgis4() {
  exige_fixture
  construir qcensosbo-qa:qgis4 Dockerfile.qgis4
  echo "▸ Suite en QGIS 4 (Qt6/PyQt6)"
  correr qcensosbo-qa:qgis4 python3 scripts/qa_headless.py
}

tarea_instalacion() {
  if [ ! -f dist/qcensosbo.zip ]; then
    echo "✗ falta dist/qcensosbo.zip. Genéralo con: python scripts/build_release.py" >&2
    return 1
  fi
  construir qcensosbo-qa:qgis4 Dockerfile.qgis4
  echo "▸ El ZIP publicable instala y arranca (QGIS 4)"
  correr qcensosbo-qa:qgis4 python3 scripts/qa_instalacion.py
}

tarea_e2e() {
  construir qcensosbo-qa:qgis4 Dockerfile.qgis4
  echo "▸ End-to-end con DuckDB y capas reales, en QGIS 4"
  correr qcensosbo-qa:qgis4 python3 scripts/qa_e2e.py
}

case "${1:-todo}" in
  check) tarea_check ;;
  min)   tarea_min ;;
  qgis4) tarea_qgis4 ;;
  e2e)   tarea_e2e ;;
  instalacion) tarea_instalacion ;;
  todo)  tarea_check; tarea_min; tarea_qgis4; tarea_e2e; tarea_instalacion ;;
  *)     echo "Uso: $0 {check|min|qgis4|e2e|instalacion|todo}"; exit 2 ;;
esac
