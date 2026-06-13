# Q-CensosBo

Complemento de **QGIS** para explorar y mapear los microdatos de los censos de población de
Bolivia (**1976, 1992, 2001, 2012 y 2024**) directamente sobre el mapa.

📖 **Sitio y documentación:** <https://lab-tecnosocial.github.io/q-censosbo/>

## Instalación

**Método A — Repositorio QGIS (recomendado, con auto-actualización):**
en QGIS → *Complementos → Administrar e instalar… → Configuración → Repositorios → Añadir*:

```
https://lab-tecnosocial.github.io/q-censosbo/plugins.xml
```

Luego busca **Q-CensosBo** en la lista e instálalo.

**Método B — ZIP:** descarga
[`qcensosbo.zip`](https://lab-tecnosocial.github.io/q-censosbo/qcensosbo.zip) e instálalo con
*Complementos → Instalar a partir de ZIP*.

Requisitos: QGIS ≥ 3.28 e internet. DuckDB se instala solo la primera vez.

## Estructura del repositorio

```
qcensosbo/              El plugin (código + geometrías). Es lo que se empaqueta en el ZIP.
docs/                   Sitio/documentación (MkDocs Material).
scripts/build_release.py   Empaqueta qcensosbo/ → dist/qcensosbo.zip y genera dist/plugins.xml.
.github/workflows/      CI: construye el sitio + ZIP y publica en GitHub Pages.
mkdocs.yml              Configuración del sitio.
```

## Desarrollo

Enlaza el plugin a tu perfil de QGIS (macOS):

```bash
ln -s "$(pwd)/qcensosbo" \
  "$HOME/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/qcensosbo"
```

Empaquetar localmente (genera `dist/qcensosbo.zip` y `dist/plugins.xml`):

```bash
python scripts/build_release.py
```

Previsualizar el sitio:

```bash
pip install mkdocs-material
mkdocs serve
```

## Publicación

- Cada *push* a `main` reconstruye el sitio y el ZIP y los publica en GitHub Pages.
- Para una versión: sube `version` en `qcensosbo/metadata.txt`, crea un tag `vX.Y.Z` y haz
  push del tag → se publica un *GitHub Release* con el ZIP adjunto.

> Tras el primer push, habilita una vez **Settings → Pages → Source: GitHub Actions**.

## Licencia

GPL-3.0. Ver [LICENSE](LICENSE).
