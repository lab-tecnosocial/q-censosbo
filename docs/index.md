# Q-CensosBo

**Q-CensosBo** es un complemento de **QGIS** para explorar y mapear los microdatos de los
censos de población de Bolivia (**1976, 1992, 2001, 2012 y 2024**) directamente sobre el mapa,
sin tener que descargar archivos pesados.

[Descargar plugin (.zip)](https://lab-tecnosocial.github.io/q-censosbo/qcensosbo.zip){ .md-button .md-button--primary }
[Cómo instalar](instalacion.md){ .md-button }

## Qué hace

- **Consulta remota y veloz**: lee los microdatos en formato Parquet alojados en GitHub Releases
  usando DuckDB; una agregación transfiere unos pocos MB en vez del archivo completo.
- **Indicadores por departamento o municipio**: conteo, media, mediana, suma, desviación
  estándar, **moda** (categoría más frecuente) y **porcentaje de una categoría**.
- **Reconoce el tipo de variable** (categórica o numérica) desde el diccionario oficial y
  muestra **etiquetas legibles** (p. ej. `1 → Quechua`) en vez de códigos.
- **Mapas coropléticos** con leyenda apropiada y un **resumen del resultado** que indica el
  valor nacional y la distribución entre unidades.

## Cómo funciona

1. Eliges **año**, **tabla**, **nivel** (departamental/municipal) y **variable**.
2. **`1 · Consultar`** calcula la agregación y muestra el resumen del resultado.
3. **`2 · Generar mapa`** dibuja la capa en QGIS, ya estilizada.

Más detalles en [Uso](uso.md) y en las [fuentes de datos](datos.md).

!!! note "Requisitos"
    QGIS 3.28 o superior y conexión a internet. DuckDB (el motor de consulta) se instala
    automáticamente la primera vez que abres el panel.
