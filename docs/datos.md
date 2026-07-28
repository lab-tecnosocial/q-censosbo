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
- **Manzano/Comunidad** (268.604 unidades, solo CPV-2024) — ver abajo.

El código geográfico se arma como `idep` (2 díg.) para departamento e `idep+iprov+imun`
(6 díg.) para municipio, y se une a las geometrías incluidas en el plugin
(`geo_departamentos.geojson`, `geo_municipios.geojson`).

## Manzanos y comunidades (CPV-2024)

El nivel más fino del censo: **268.604 unidades censales** —manzanos urbanos y comunidades
rurales— con **194 indicadores** cada una, tomados de la ficha resumen que el INE publica en su
geoportal. No son microdatos: son conteos ya agregados por unidad.

!!! note "194 indicadores, 245 opciones en el selector"
    La ficha del INE trae 194 columnas, casi todas separadas por sexo (`_h` / `_m`). El plugin
    añade **51 opciones derivadas** que suman ambos sexos (lo que normalmente se quiere mapear),
    así que el selector muestra **245** entradas para las mismas 194 columnas de origen.

Dos tablas:

- **Ficha de indicadores** — los 194 indicadores (población por edad y sexo, educación, salud,
  migración, empleo, actividad económica, vivienda, servicios básicos, TIC, materiales,
  hacinamiento y tipo de hogar), más las 51 derivadas de ambos sexos.
- **Unidades censales** — el universo completo, con población, viviendas y si el INE libera la
  ficha de esa unidad.

Cosas a tener en cuenta:

- **Cobertura**: el INE reserva la ficha de las unidades con poca población, así que solo
  150.744 de las 268.604 la tienen (deja fuera al 47 % de los manzanos, pero cubre el **92 % de
  la población** y el 90 % de las viviendas). Las unidades sin ficha se dibujan **sin dato**.
- **Se mapea de a un municipio**: hay que elegir departamento y municipio (el país entero son
  268.604 unidades). Es la misma restricción que `mapa_man()` en el paquete de R.
- **Las comunidades rurales son puntos**, no polígonos: el INE publica un centro aproximado.
  Si se piden las dos áreas, el plugin genera **dos capas agrupadas** —manzanos en polígono,
  comunidades en punto— con la **misma escala de color**, para que se puedan comparar.
- **Medidas**: *Total (conteo)* o *% del total del bloque*. El porcentaje divide el indicador
  entre el total de su bloque temático (p. ej. `serv_agua_caneria / serv_agua_total`) sumando
  antes numerador y denominador, que es la forma correcta de agregar una proporción.
- **Los totales agregados están incompletos, y de forma desigual**: al sumar un indicador por
  municipio o departamento solo entran las unidades con ficha liberada. La cobertura poblacional
  varía del **85 %** (Oruro) al **94 %** (La Paz) —y por área, del 89,5 % urbano al 99,2 % rural,
  porque lo que el INE reserva son sobre todo manzanos urbanos pequeños—, así que un mapa de
  totales queda sesgado de manera no uniforme. Para comparar territorios usa el porcentaje.
- Los datos viven en un release aparte (`data-fichas-v1.0.0`) y las geometrías se descargan y
  cachean en `~/.censosbo_qgis/fichas/` (de 0,3 a 6,7 MB por departamento).

## Diccionarios

Cada release de microdatos incluye:

- `diccionario_variables.parquet` — nombre, etiqueta y **tipo** (`categorica`/`numerica`/`texto`)
  de cada variable. Es la fuente de verdad para clasificar y describir las variables.
- `diccionario_etiquetas.parquet` — el mapeo **código → etiqueta** de las variables categóricas
  (p. ej. `1 → Quechua`).

El catálogo de los indicadores de ficha (etiqueta, bloque temático y denominador de cada uno)
viene **empaquetado con el plugin**, así que ese selector se llena al instante y sin conexión.

## Notas y limitaciones

- **1976**: solo a nivel **departamental** (el censo usa cantón, no municipio actual).
- **2012**: no incluye la tabla de **mortalidad**.
- **Manzano/Comunidad**: solo **CPV-2024**, y solo en las tablas de fichas (en los microdatos la
  unidad es la persona o la vivienda, no el manzano).
- El total de **viviendas** de la tabla de unidades da un 0,23 % menos que la tabla de viviendas
  de microdatos: el INE las cuenta distinto en el geoportal. Para el total de viviendas de un
  territorio conviene usar la tabla **Viviendas**.
- **Cuatro municipios sin geometría**: los datos de **2024 y 2001** traen 343 municipios, pero los
  GeoJSON incluidos en el plugin tienen 339. Faltan cuatro de creación reciente —`031304`,
  `050405`, `051204`, `080901`— que no se pintan. 2012 y 1992 se mapean al 100 %. El panel avisa
  del desajuste en el resumen y al generar el mapa, y el algoritmo de Processing lo registra en el
  log.
- **Porcentaje de una categoría**: se calcula sobre los **casos con dato** de la variable, no sobre
  todos los registros (77 de las 119 columnas de 2024/personas tienen menos del 99 % de cobertura,
  porque la pregunta solo aplica a un subgrupo). El resumen indica siempre cuántos casos entraron
  en el denominador.
- Los códigos pueden venir con ceros a la izquierda en los datos (`"028"`) y sin ellos en el
  diccionario (`"28"`); el plugin los empareja correctamente.
