# Datos

## Fuente

Q-CensosBo usa los microdatos del paquete [**censosbo**](https://github.com/lab-tecnosocial/censosbo),
que publica los censos de población de Bolivia en formato **Parquet** como *GitHub Releases*.
El plugin los consulta de forma remota con DuckDB (sin descargar el archivo completo) o, si ya
están en caché local, los lee directamente.

Censos disponibles: **1976, 1992, 2001, 2012 y 2024**.

## Niveles geográficos

- **Departamental** (9 unidades).
- **Municipal** (≈339 unidades).

El código geográfico se arma como `idep` (2 díg.) para departamento e `idep+iprov+imun`
(6 díg.) para municipio, y se une a las geometrías incluidas en el plugin
(`geo_departamentos.geojson`, `geo_municipios.geojson`).

## Diccionarios

Cada release incluye:

- `diccionario_variables.parquet` — nombre, etiqueta y **tipo** (`categorica`/`numerica`/`texto`)
  de cada variable. Es la fuente de verdad para clasificar y describir las variables.
- `diccionario_etiquetas.parquet` — el mapeo **código → etiqueta** de las variables categóricas
  (p. ej. `1 → Quechua`).

## Notas y limitaciones

- **1976**: solo a nivel **departamental** (el censo usa cantón, no municipio actual).
- **2012**: no incluye la tabla de **mortalidad**.
- **2001 (municipal)**: algunos municipios de 2001 no coinciden con la división municipal
  actual de las geometrías, por lo que no se pintan (cobertura ≈ 99%).
- Los códigos pueden venir con ceros a la izquierda en los datos (`"028"`) y sin ellos en el
  diccionario (`"28"`); el plugin los empareja correctamente.
