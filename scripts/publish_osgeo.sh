#!/usr/bin/env bash
# Publica el ZIP en el repositorio oficial de complementos de QGIS.
#
# Usa la API con token del sitio, que es la vía sin contraseña:
#
#   POST https://plugins.qgis.org/plugins/api/<slug>/version/add/
#   Authorization: Bearer <token>
#   multipart: package=<zip>
#
# El token se crea una vez en la página del plugin (botón «Tokens») y se guarda
# como secreto del repositorio; nunca va en el código ni se imprime aquí. Se lee
# de la variable de entorno OSGEO_PLUGIN_TOKEN.
#
# La versión que se sube es la de metadata.txt: el sitio la rechaza si ya existe,
# así que hay que bumpear antes (lo hace scripts/release.py).
#
# Uso:
#   OSGEO_PLUGIN_TOKEN=... scripts/publish_osgeo.sh
#   OSGEO_PLUGIN_TOKEN=... scripts/publish_osgeo.sh dist/qcensosbo.zip
set -euo pipefail

SLUG="qcensosbo"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ZIP="${1:-$ROOT/dist/qcensosbo.zip}"
API="https://plugins.qgis.org/plugins/api/${SLUG}/version/add/"

if [ -z "${OSGEO_PLUGIN_TOKEN:-}" ]; then
  echo "✗ Falta OSGEO_PLUGIN_TOKEN." >&2
  echo "  Crea un token en https://plugins.qgis.org/plugins/${SLUG}/ (botón «Tokens»)" >&2
  echo "  y guárdalo como secreto:  gh secret set OSGEO_PLUGIN_TOKEN" >&2
  exit 2
fi

if [ ! -f "$ZIP" ]; then
  echo "✗ No existe $ZIP. Genéralo con: python scripts/build_release.py" >&2
  exit 2
fi

version=$(grep -E '^version=' "$ROOT/qcensosbo/metadata.txt" | cut -d= -f2)
echo "▸ Subiendo $(basename "$ZIP") como versión ${version} a ${API}"

respuesta=$(mktemp)
trap 'rm -f "$respuesta"' EXIT

# --fail-with-body no está en curl viejos, así que se mira el código a mano.
codigo=$(curl -sS -o "$respuesta" -w '%{http_code}' \
  -X POST \
  -H "Authorization: Bearer ${OSGEO_PLUGIN_TOKEN}" \
  -F "package=@${ZIP}" \
  "$API")

if [ "$codigo" -ge 200 ] && [ "$codigo" -lt 300 ]; then
  echo "✓ Subida correcta (HTTP $codigo)"
  cat "$respuesta"; echo
  echo "  Revisa el escaneo de seguridad en:"
  echo "  https://plugins.qgis.org/plugins/${SLUG}/version/${version}/#security-tab"
else
  echo "✗ La subida falló (HTTP $codigo). Respuesta del servidor:" >&2
  cat "$respuesta" >&2; echo >&2
  exit 1
fi
