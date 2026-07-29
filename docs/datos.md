# Datos

## Fuente

Q-CensosBo usa los microdatos del paquete [**censosbo**](https://github.com/lab-tecnosocial/censosbo),
que publica los censos de población de Bolivia en formato **Parquet** como *GitHub Releases*.
El plugin los consulta de forma remota con DuckDB (sin descargar el archivo completo) o, si ya
están en caché local, los lee directamente.

Censos disponibles: **1976, 1992, 2001, 2012 y 2024**.

## Niveles geográficos

- **Departamental** (9 unidades).
- **Municipal** (343 unidades).
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

## Geometrías

Los polígonos de departamentos y municipios vienen **empaquetados con el plugin**, así que los mapas
de esos niveles no necesitan descargar nada. Salen de `censosbo`, que construye la capa municipal
cruzando los límites de [SDSN Bolivia](https://sdsnbolivia.org/) con los códigos y nombres del INE,
y deriva la departamental por disolución de la municipal, de modo que los bordes coinciden
exactamente.

Cada municipio trae además su **superficie**, que es la que usa la casilla *Mostrar por km²*. Ojo con
lo que mide: la suma nacional da unos 1.063.500 km² frente a los ~1.098.600 de la superficie oficial
de Bolivia, porque los grandes cuerpos de agua y salares (Titicaca, Poopó, Uru Uru, Salar de Uyuni)
no pertenecen a ningún municipio. Las densidades de La Paz, Oruro y Potosí salen por eso algo por
encima de las calculadas con la superficie oficial del departamento.

## Diccionarios

Cada release de microdatos incluye:

- `diccionario_variables.parquet` — nombre, etiqueta, **tipo**
  (`categorica`/`numerica`/`texto`), **tema** del catálogo del INE y **universo** (a quién se le hizo
  la pregunta) de cada variable. Es la fuente de verdad para clasificar y describir las variables.
- `diccionario_etiquetas.parquet` — el mapeo **código → etiqueta** de las variables categóricas
  (p. ej. `1 → Quechua`).

Vienen **empaquetados con el plugin**, y por eso están disponibles al instante y sin conexión:

- El **catálogo de los indicadores de ficha** (etiqueta, bloque temático y denominador de cada uno).
- La **documentación conceptual del INE** de 445 variables de los cinco censos: la definición
  oficial, la pregunta tal como se leyó en campo, el universo redactado y la regla de construcción
  de las variables derivadas. Sale de los diccionarios DDI del catálogo
  [ANDA](https://anda.ine.gob.bo/) del INE y es lo que se ve al pasar el ratón por el selector de
  variable.

### Los diccionarios se actualizan solos

Antes de usar un diccionario ya descargado, el plugin comprueba con el servidor si cambió —una
consulta pequeña, sin bajar el archivo— y solo lo renueva si hace falta. Así, cuando el INE corrige
una etiqueta o el paquete de datos añade información, la ves sin hacer nada. Sin conexión se usa la
copia local.

## Notas y limitaciones

- **1976**: solo a nivel **departamental** (el censo usa cantón, no municipio actual).
- **2012**: no incluye la tabla de **mortalidad**.
- **Manzano/Comunidad**: solo **CPV-2024**, y solo en las tablas de fichas (en los microdatos la
  unidad es la persona o la vivienda, no el manzano).
- **La tabla de Viviendas cuenta el universo oficial del INE, no todas sus filas.** La tabla de
  viviendas del censo incluye registros de personas censadas *fuera* de una vivienda —en la calle
  y en tránsito—, que el INE no cuenta como viviendas en ningún tabulado: son 10.287 de los
  4.490.488 registros de 2024. El plugin los descuenta, así que un total de viviendas de 2024 da
  **4.480.201**, la cifra oficial. Los censos anteriores traen la misma categoría con otro nombre
  (1992 *Ambulante*, 2001 *Transeúntes*, 2012 *En tránsito* y *Persona que vive en la calle*); en
  1976 no se preguntó. El resumen del resultado lo declara cuando aplica.
- Con eso, el total de **viviendas** de la tabla de unidades (geoportal) y el de la tabla de
  **Viviendas** (microdatos) **coinciden exactamente**, municipio a municipio. Cualquiera de las
  dos sirve. (Hasta la v0.5.0 diferían un 0,23 % y aquí se decía que «el INE las cuenta distinto
  en el geoportal»: era falso, la diferencia eran esos registros de calle y tránsito.)
- **Cartografía municipal completa (343)**: los mapas municipales de 2024 y 2001 se pintan al
  100 %. Los cuatro municipios autónomos indígenas creados desde 2016 —TIOC-Raqaypampa,
  San Pedro de Macha, TIOC-Jatun Ayllu Yura y TIOC-Territorio Indígena Multiétnico— ya tienen su
  propio polígono; antes su territorio se dibujaba dentro del municipio del que se separaron, así que
  sus datos quedaban **atribuidos al municipio equivocado**. También desaparecieron las franjas
  blancas que se veían entre municipios al hacer zoom.
  Al mapear **censos anteriores a 2012** puede haber códigos que no existen en la división actual: el
  panel lo avisa en el resumen y el algoritmo de Processing lo registra en el log.
- **Porcentaje de una categoría**: se calcula sobre los **casos con dato** de la variable, no sobre
  todos los registros (77 de las 119 columnas de 2024/personas tienen menos del 99 % de cobertura,
  porque la pregunta solo aplica a un subgrupo). El resumen indica siempre cuántos casos entraron
  en el denominador.
- Los códigos pueden venir con ceros a la izquierda en los datos (`"028"`) y sin ellos en el
  diccionario (`"28"`); el plugin los empareja correctamente.
