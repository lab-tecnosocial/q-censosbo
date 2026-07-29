# Uso

El panel se divide en **Datos**, **Análisis** y **Resumen del resultado**, y dos pasos:
**`1 · Consultar`** y **`2 · Generar mapa`**.

Los dos botones tienen el mismo peso, y el relleno señala cuál toca ahora: al principio está
destacado el `1`, y en cuanto la consulta devuelve resultado el énfasis pasa al `2`. Si cambias
cualquier parámetro, el resultado se invalida y el énfasis vuelve al `1`.

## Tu primer mapa en un minuto

Con los valores que trae el panel al abrirse (2024 · Personas · Departamental · Conteo de
registros) solo hay que pulsar **`1 · Consultar`** y luego **`2 · Generar mapa`**: sale un mapa de
la población de los 9 departamentos. Desde ahí ya puedes cambiar campos de uno en uno.

Tres recetas concretas:

| Quiero mapear… | Año | Tabla | Nivel | Variable | Agregación |
|---|---|---|---|---|---|
| Población por municipio | 2024 | Personas | Municipal | Conteo de registros | Conteo |
| Edad promedio por municipio | 2024 | Personas | Municipal | `p26_edad` | Media |
| % de agua por cañería en los manzanos de Cochabamba | 2024 | Ficha de indicadores | *(fijo)* | Servicios básicos · Agua por cañería de red | % del total del bloque |

---

## Referencia de campos

Cada campo tiene esta misma explicación como **tooltip**: pasa el ratón por su rótulo dentro de
QGIS.

### Datos

#### Año

El censo de población y vivienda del que salen los datos: **2024, 2012, 2001, 1992 o 1976**.
Cambiar el año repuebla las tablas y los niveles, porque no todos los censos tienen lo mismo.

*Ejemplo:* `2024` para el CPV-2024, el más reciente.

#### Tabla

**Qué unidad describe cada registro** del archivo. Es lo que determina qué puedes preguntar.

| Tabla | Cada fila es… | Sirve para |
|---|---|---|
| **Personas** | una persona empadronada | sexo, edad, idioma, educación, empleo, migración |
| **Viviendas** | una vivienda | materiales, servicios básicos, tenencia, hacinamiento |
| **Emigración** | una persona del hogar que se fue del país | destino, año de salida, sexo, edad |
| **Mortalidad** | un fallecimiento declarado en el hogar | edad, sexo, mes y año, parto |
| **Ficha de indicadores** | un manzano o una comunidad | los 194 indicadores ya agregados del INE |
| **Unidades censales** | un manzano o una comunidad | población, viviendas y si tiene ficha |

Las tablas disponibles cambian con el año: 2012 no tiene Mortalidad, 2001 y 1976 solo tienen
Personas y Viviendas, y las dos tablas de manzano/comunidad son exclusivas del CPV-2024.

*Ejemplo:* Viviendas + «material predominante de las paredes» para un mapa de materiales.

#### Nivel

La **unidad geográfica en la que se agrupa el resultado**: un valor por unidad, y un polígono por
valor en el mapa.

| Nivel | Unidades | Cuándo |
|---|---|---|
| **Departamental** | 9 | comparaciones nacionales, y el único nivel de 1976 |
| **Municipal** | 343 | el nivel habitual de trabajo |
| **Manzano/Comunidad** | 268.604 | análisis intraurbano, solo con las tablas de fichas |

**Solo aparecen los niveles que existen** para el año y la tabla elegidos. Y si solo hay uno, el
campo desaparece y en su lugar una línea dice cuál es:

- con **Ficha de indicadores** o **Unidades censales**, el nivel es siempre manzano/comunidad,
  porque esos datos *son* del manzano;
- con el censo de **1976** el nivel es siempre departamental, porque ese censo usa cantón y no el
  municipio actual.

#### Departamento

Restringe el cálculo a un departamento en lugar de a todo el país. Aparece en el nivel municipal y
en el de manzano/comunidad. El **valor de referencia** del resumen pasa a ser el departamental en
vez del nacional.

*Ejemplo:* `Cochabamba (03)` para mapear solo sus 47 municipios.

#### Municipio

El municipio que se va a mapear por manzano y comunidad. **Es obligatorio** en ese nivel: el país
entero son 268.604 unidades y no se mapean de una sola vez. La lista se filtra por el departamento
elegido y muestra el código nacional de 6 dígitos.

*Ejemplo:* `Cochabamba (030101)`, `El Alto (020105)`.

#### Área

Qué unidades censales incluir, en el nivel de manzano/comunidad:

- **Solo manzanos (urbana)** — polígonos.
- **Solo comunidades (rural)** — puntos: el INE publica un centro aproximado, no un polígono.
- **Urbana y rural** — dos capas agrupadas que **comparten la escala de color**, para poder
  comparar lo urbano con lo rural en el mismo mapa.

### Análisis

#### Tema / Bloque

**Acota la lista de variables**, para no recorrer cientos de opciones buscando una. Cambia de
nombre según la tabla:

- **Tema** en los microdatos: los temas del catálogo del INE —educación, migración, servicios
  básicos, fecundidad…—. Son **los mismos en los cinco censos**, así que también sirven para
  encontrar la variable equivalente de otro año.
- **Bloque** en las fichas de manzano y comunidad: los bloques del geoportal, que además son los
  que definen el denominador de «% del total del bloque».

Cada opción dice cuántas variables tiene. *Ejemplos:* `Educación (11)` deja 11 de las 119 variables
de personas en 2024; `Servicios básicos` deja 41 de las 245 opciones de la ficha. Con **Todos los
temas** se ve la lista completa.

Al filtrar por un bloque, las etiquetas dejan de repetir su nombre: si ya elegiste «Servicios
básicos», no hace falta que cada opción lo lleve delante.

No aparece en el modo SQL, donde no hay lista que acotar.

#### Variable

**Qué se mide.** La primera opción, **Conteo de registros**, cuenta personas o viviendas y no
necesita configurar nada más.

El resto son las columnas del archivo, con su descripción del diccionario oficial y el **tipo**
entre paréntesis, que es lo que decide qué agregaciones tienen sentido:

- `(num)` numérica — p. ej. `p26_edad (num) — 26. Cuantos años cumplidos tiene`
- `(cat)` categórica — p. ej. `p25_sexo (cat) — 25. Sexo`
- `(txt)` texto

Las columnas geográficas y técnicas (`idep`, `iprov`, `imun`, `area`, `i00`, claves de join) no se
listan: no son variables de análisis. En las tablas de fichas, las opciones vienen agrupadas por
bloque temático (`Servicios básicos · Agua por cañería de red`), porque son 245.

Al elegir una variable, debajo aparece su descripción y —cuando corresponde— **a quién se le hizo
la pregunta**:

> `40. Sabe leer y escribir`
> Se preguntó a: personas de 7 años o más.

Ese dato viene del diccionario oficial y **cambia cómo se lee el resultado**: un promedio de
escolaridad no es «de la población» si la pregunta solo se hizo a mayores de 19 años. El resumen del
resultado lo repite, para que quede en el mapa que publiques. No se muestra cuando el universo es el
obvio (todas las personas, todas las viviendas): ahí solo sería ruido.

!!! warning "Ojo al comparar años"
    El INE movió el filtro de edad de varias preguntas entre censos. `nivel_edu`, por ejemplo, se
    construyó sobre personas de 6 años o más en 1992, 4 en 2001 y **19** en 2024. Si vas a comparar
    censos, revisa el universo de cada uno antes de sacar conclusiones.

**Pasa el ratón por el selector** y verás la documentación oficial completa de la variable: qué mide
exactamente, la pregunta tal como se leyó en campo, a qué población se aplicó y —en las variables
derivadas— la regla con que el INE las construyó. Sale de los diccionarios DDI del catálogo del INE
y cubre 445 variables de los cinco censos. Es lo que conviene leer antes de dar por hecho que una
variable mide lo que su nombre sugiere.

#### Mostrar por km²

Convierte el resultado en una **densidad**: divide el valor de cada unidad entre su superficie.

Solo aparece con **Conteo** y con **Suma**, que son las únicas magnitudes en las que tiene sentido —
una edad promedio o un porcentaje «por km²» no significa nada—, y en los niveles departamental y
municipal, los únicos con superficie declarada.

*Ejemplo:* Conteo de personas por municipio + esta casilla = habitantes por km².

Activarla **no obliga a volver a consultar**: es una transformación del resultado que ya tienes.

!!! note "Qué superficie se usa"
    La que declara la cartografía municipal del censo. No incluye los grandes lagos y salares, que
    no pertenecen a ningún municipio, así que en La Paz, Oruro y Potosí la densidad sale algo por
    encima de la que se calcularía con la superficie oficial del departamento.

#### Agregación

**Cómo se resume la variable dentro de cada unidad geográfica.** Las opciones cambian según el
tipo (ver [la tabla de abajo](#tipos-de-variable-y-agregaciones)).

*Ejemplo:* `Media` de `p26_edad` a nivel municipal = la edad promedio de cada municipio.

#### Categoría

Solo con **Porcentaje de una categoría**: cuál de las categorías se va a mapear. Sale del
diccionario de etiquetas del censo, con el código y su significado. Si la variable no está en ese
diccionario, el plugin lee los valores distintos del propio archivo.

*Ejemplo:* en `p25_sexo`, elegir `2 — Hombre` mapea el % de hombres.

#### Clasificación

**Cómo se agrupan los valores en las clases de color** del mapa. Aparece en todos los mapas
graduados, es decir en todos menos el de Moda.

| Método | Qué hace | Cuándo usarlo |
|---|---|---|
| **Natural Breaks (Jenks)** | busca los cortes naturales de los datos | el mejor punto de partida para ver patrones |
| **Cuantiles** | la misma cantidad de unidades en cada clase | para ordenar; engañoso si los valores están concentrados |
| **Intervalo igual** | clases del mismo ancho | bien con porcentajes y escalas conocidas |
| **Desviación estándar** | clases según la distancia a la media | para ver quién se separa del promedio |

El resumen muestra **las mismas clases** que tendrá la leyenda del mapa, así que puedes probar
métodos y ver el reparto antes de dibujar. Cambiar la clasificación **no** obliga a volver a
consultar: solo afecta al estilo.

!!! note "Clases de menos"
    Con pocas unidades, Jenks puede devolver menos de 5 clases (con los 9 departamentos suele dar
    3). Es correcto: el plugin descarta las clases vacías que el algoritmo genera en ese caso, en
    lugar de mostrarlas repetidas en la leyenda.

---

## Tipos de variable y agregaciones

El tipo proviene del diccionario oficial (`categorica` / `numerica` / `texto`):

- **Conteo de registros** — cuántas personas o viviendas hay en cada unidad geográfica.
- **Categórica** (p. ej. sexo, pueblo indígena):
    - **Porcentaje de una categoría** — eliges una categoría y el mapa muestra su % por unidad.
    - **Moda (categoría más frecuente)** — mapa por colores, con leyenda de etiquetas.
- **Numérica** (p. ej. edad):
    - **Media**, **Mediana**, **Suma** y **Desviación estándar**.
- **Indicadores de ficha** (manzanos y comunidades, que ya son conteos):
    - **Total (conteo)** — suma el indicador en la unidad de análisis.
    - **% del total del bloque** — lo divide entre el total de su bloque temático (p. ej.
      «Agua por cañería de red» entre `serv_agua_total`, el total del bloque de agua).

En los mapas graduados —todos menos el de **Moda**— puedes elegir el método de
**clasificación** (Natural Breaks/Jenks, cuantiles, intervalo igual, desviación estándar). El
resumen muestra **las mismas clases** que tendrá la leyenda del mapa.

### Sobre qué se calcula el porcentaje

El **porcentaje de una categoría** se calcula sobre los **casos con dato**, no sobre todos los
registros. Muchas preguntas del censo solo aplican a un subgrupo (mujeres en edad fértil,
ocupados, quienes asisten a un centro educativo…), así que el resultado es «el % entre quienes
respondieron». El resumen lo dice explícitamente:

> *Calculado sobre 4.529.497 casos con dato (39,9% de 11.365.333 registros). El resto no
> respondió o la pregunta no le aplica, y queda fuera del denominador.*

Son **dos cosas distintas y complementarias**, y conviene no confundirlas:

| | Qué dice | De dónde sale |
|---|---|---|
| **Se preguntó a** | El universo *de diseño*: a quién iba dirigida la pregunta en el cuestionario | El diccionario oficial del INE |
| **Calculado sobre** | Los casos *con dato* que efectivamente entraron en el cálculo | Se cuenta en la consulta |

El primero explica el segundo. Si una pregunta se hizo a personas de 7 años o más, es normal que los
casos con dato sean bastante menos que el total de registros: el resto no es «no respuesta», es
población fuera del universo.

Si una variable no tiene catálogo de categorías en el diccionario, el plugin lee sus valores
distintos del propio archivo. Cuando ni así hay categorías (dominio demasiado grande, como una
fecha), **Porcentaje** se deshabilita y se explica por qué; usa **Moda** en su lugar.

## Mapas de manzano y comunidad (CPV-2024)

Al elegir la tabla **Ficha de indicadores** o **Unidades censales**, el nivel pasa a ser
manzano/comunidad automáticamente —esos datos *son* del manzano, así que el campo **Nivel**
desaparece— y aparecen tres campos nuevos:

1. **Departamento** y **Municipio** (es obligatorio: el país entero son 268.604 unidades).
2. **Área**: urbana (manzanos, polígonos), rural (comunidades, puntos) o ambas.
3. Consulta y genera el mapa.

El resultado es un **grupo de capas**:

- los **manzanos** (polígonos) y las **comunidades** (puntos), que comparten la escala de color
  para que urbano y rural sean comparables;
- el **límite del municipio** como capa de contexto al fondo, para que los manzanos no queden
  "al aire" y se vea dónde caen dentro del territorio. Si no la quieres, desmárcala en el panel
  de capas.

Las unidades cuya ficha el INE reserva por poca población quedan **sin dato** (ver
[Datos](datos.md)). La primera vez, generar el mapa descarga las geometrías del departamento
(0,3–6,7 MB) y las cachea en `~/.censosbo_qgis/fichas/`.

!!! tip "¿Y si quiero este indicador por municipio?"
    Usa los **microdatos**, no las fichas: las tablas Personas y Viviendas cubren el 100 % de la
    población, mientras las fichas solo llegan al 92 % y de forma desigual entre departamentos (del
    85 % en Oruro al 94 % en La Paz), lo que deformaría el mapa. Todo lo de la ficha se puede
    recalcular desde los microdatos —incluidos materiales (`v03_pared`, `v05_techo`, `v06_piso`),
    tipología de hogar (`tip_hog`) y hacinamiento (`tot_pers` entre `v14_dormit`, con el
    [modo SQL avanzado](#modo-sql-avanzado))—. La ficha te ahorra el cálculo a cambio de peor
    cobertura, así que solo conviene en su propio nivel: el manzano y la comunidad.

## El resumen del resultado

Tras **Consultar** verás:

- **Qué se está mapeando**, en negrita y con la etiqueta legible de la variable (el nombre
  técnico va entre paréntesis).
- El **valor de referencia** del territorio consultado: nacional, departamental o municipal
  según los filtros aplicados.
- La **base de cálculo**, cuando se trata de un porcentaje (ver arriba).
- **La distribución** entre unidades:
    - un *ranking* con barras cuando son pocas (hasta 120: los departamentos, o los municipios de
      un departamento). Las barras son **proporcionales desde cero**, así que su longitud se puede
      leer como el valor;
    - las **clases del mapa** cuando son muchas (todos los municipios del país, o los manzanos de
      un municipio): cuántas unidades caen en cada clase de color, con los cortes del método de
      clasificación elegido.
- Avisos cuando hacen falta: unidades sin geometría, o la cobertura de las fichas.

!!! note "Cuando un código no tiene polígono"
    La cartografía del plugin es la división municipal vigente, con los **343** municipios del
    CPV-2024, así que 2024 y 2001 se pintan completos. Al mapear censos anteriores puede haber
    códigos que ya no existan en esa división: el resumen y el mensaje al generar el mapa dicen
    cuántas unidades quedan sin pintar y cuáles son.

## Modo SQL avanzado

Activa **Modo SQL avanzado** cuando el indicador que quieres no sale de ninguna combinación de
variable + agregación: una razón entre dos variables, un tramo de edad, un índice. Escribes la
fórmula del campo `valor` y el plugin le añade el `GROUP BY` geográfico y los filtros geográficos
que tengas puestos. Sustituye a **Variable** y **Agregación**, que se ocultan.

```sql
-- edad promedio (equivale a Variable p26_edad + Media)
AVG(p26_edad)

-- % de hombres, sobre los registros con dato de sexo
100.0 * COUNT(CASE WHEN p25_sexo = 1 THEN 1 END) / NULLIF(COUNT(p25_sexo), 0)

-- índice de dependencia: (menores de 15 + 65 y más) / población en edad de trabajar
100.0 * COUNT(CASE WHEN p26_edad < 15 OR p26_edad >= 65 THEN 1 END)
      / NULLIF(COUNT(CASE WHEN p26_edad BETWEEN 15 AND 64 THEN 1 END), 0)

-- razón de sexos: hombres por cada 100 mujeres
100.0 * COUNT(CASE WHEN p25_sexo = 1 THEN 1 END)
      / NULLIF(COUNT(CASE WHEN p25_sexo = 2 THEN 1 END), 0)
```

Con las tablas de fichas la expresión opera sobre conteos ya agregados, así que se usa `SUM`:

```sql
-- % de viviendas con internet en el manzano
100.0 * SUM(tic_internet) / NULLIF(SUM(tic_total), 0)
```

Notas prácticas:

- Usa `NULLIF(x, 0)` en los denominadores para no dividir por cero (devuelve sin dato en su lugar).
- El resumen repite la expresión bajo el título, y **no** añade el signo `%`: el plugin no puede
  saber si tu fórmula devuelve un porcentaje.
- La clasificación sigue disponible: el resultado siempre es un mapa graduado.
- Cualquier función de DuckDB vale (`AVG`, `SUM`, `COUNT`, `MEDIAN`, `CASE`, `NULLIF`…).

## Desde Processing

El plugin registra dos algoritmos en la caja de herramientas, para modelos gráficos y
procesamiento por lotes:

- **Calcular indicador censal** — por departamento o municipio, cualquier censo.
- **Indicador por manzano/comunidad (CPV-2024)** — para un municipio y un área.

Diferencias con el panel, a tener en cuenta:

- Las capas salen **sin simbología** y sin la capa de contexto; aplícalas en QGIS.
- La **variable se escribe a mano** (`p26_edad`, `serv_agua_caneria`): no hay lista. Los nombres de
  los indicadores de ficha están en `qcensosbo/data/dicc_fichas.csv` dentro del plugin.
- El **municipio** se indica con su código de 6 dígitos (`020105` para El Alto).
- El algoritmo de manzanos produce **una** geometría por ejecución, así que el área no es opcional.
- «Calcular indicador censal» trabaja solo con microdatos (Personas, Viviendas, Emigración,
  Mortalidad); las fichas van por el otro algoritmo, siempre a nivel de manzano y comunidad.

A cambio, sirven para lo que el panel no puede: modelos gráficos y **procesamiento por lotes** (el
mismo indicador en 20 municipios de una pasada).

## Caché en disco

El plugin guarda en `~/.censosbo_qgis/`: los diccionarios por año, las geometrías de
manzanos y comunidades (`fichas/`) y las capas generadas (`capas/`). Los microdatos **no** se
descargan: DuckDB los consulta en remoto.

**Los diccionarios y las geometrías se actualizan solos.** Antes de usar un archivo cacheado, el
plugin comprueba con el servidor si cambió (una consulta pequeña, sin descargar el archivo) y solo
lo vuelve a bajar si hace falta. Así, cuando el INE corrige una etiqueta o el paquete de datos añade
información, la ves sin tener que hacer nada. Sin conexión se usa la copia local, y el plugin sigue
funcionando.

Si aun así quieres partir de cero, puedes borrar la carpeta: se vuelve a descargar lo necesario.
