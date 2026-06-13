# Q-CensosBo

Complemento de **QGIS** para explorar y mapear los microdatos de los censos de población de
Bolivia (**1976, 1992, 2001, 2012 y 2024**) directamente sobre el mapa, sin descargar archivos
pesados.

📖 **Sitio y documentación:** <https://lab-tecnosocial.github.io/q-censosbo/>

## Qué hace

- Consulta los microdatos (en GitHub Releases) de forma remota y veloz con DuckDB.
- Calcula indicadores por **departamento** o **municipio**: conteo, media, mediana, suma,
  desviación, moda y porcentaje de una categoría.
- Reconoce variables categóricas y numéricas y muestra etiquetas legibles.
- Genera mapas coropléticos con leyenda apropiada y un resumen del resultado.

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

Guía de uso completa en la [documentación](https://lab-tecnosocial.github.io/q-censosbo/uso/).

## Datos

Microdatos del paquete [**censosbo**](https://github.com/lab-tecnosocial/censosbo) (censos de
Bolivia, formato Parquet). Detalle de fuentes, niveles y notas en la
[documentación](https://lab-tecnosocial.github.io/q-censosbo/datos/).

## Licencia

GPL-3.0. Ver [LICENSE](LICENSE).
