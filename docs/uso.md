# Uso

El panel se divide en **Datos**, **Análisis** y **Resumen del resultado**, y dos botones:
**`1 · Consultar`** y **`2 · Generar mapa`**.

## Flujo de trabajo

1. **Datos**
    - **Año** del censo (1976, 1992, 2001, 2012, 2024).
    - **Tabla** (Personas, Viviendas, …; varía según el año).
    - **Nivel**: Departamental, Municipal o Manzano/Comunidad. *(1976 solo está disponible a
      nivel departamental; Manzano/Comunidad, solo en las tablas de fichas del CPV-2024.)*
    - **Departamento** (a nivel municipal y de manzano), **Municipio** y **Área** (a nivel de
      manzano/comunidad).
2. **Análisis**
    - **Variable**: se listan las columnas de la tabla con su descripción del diccionario
      oficial. En las tablas de fichas, los 245 indicadores agrupados por bloque temático.
    - **Agregación**: las opciones se ajustan al **tipo** de la variable (ver abajo).
3. **`1 · Consultar`**: calcula la agregación por unidad geográfica y muestra el
   **resumen del resultado**.
4. **`2 · Generar mapa`**: dibuja la capa coroplética en QGIS con la simbología elegida.
   No vuelve a consultar; usa el resultado ya calculado.

!!! info
    Si cambias algún parámetro, debes volver a **Consultar** antes de **Generar mapa**.

## Tipos de variable y agregaciones

El tipo proviene del diccionario oficial (`categorica` / `numerica` / `texto`):

- **Categórica** (p. ej. sexo, pueblo indígena):
    - **Moda** — la categoría más frecuente por unidad (mapa por colores, con leyenda de
      etiquetas).
    - **Porcentaje** — eliges una categoría y el mapa muestra su % por unidad.
- **Numérica** (p. ej. edad):
    - **Media**, **Mediana**, **Suma** y **Desviación estándar**.
- **Indicadores de ficha** (manzanos y comunidades, que ya son conteos):
    - **Total (conteo)** — suma el indicador en la unidad de análisis.
    - **% del total del bloque** — lo divide entre el total de su bloque temático (p. ej.
      «Agua por cañería de red» entre el total de viviendas con dato de agua).

En los mapas graduados puedes elegir el método de **clasificación** (Natural Breaks/Jenks,
cuantiles, intervalo igual, desviación estándar).

## Mapas de manzano y comunidad (CPV-2024)

Con la tabla **Ficha de indicadores** o **Unidades censales** se habilita el nivel
**Manzano/Comunidad**:

1. Elige **departamento** y **municipio** (es obligatorio: el país entero son 268.604 unidades).
2. Elige el **área**: urbana (manzanos, polígonos), rural (comunidades, puntos) o ambas.
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

## El resumen del resultado

Tras **Consultar** verás:

- **Qué se está mapeando** (una línea descriptiva del indicador).
- El **valor de referencia** del territorio consultado: nacional, departamental o municipal
  según los filtros aplicados.
- **La distribución** entre unidades: un *ranking* con barras cuando son pocas (hasta 120,
  p. ej. los departamentos o los municipios de uno) o un *histograma* por rangos cuando son
  muchas (todos los municipios del país, o los manzanos de un municipio).

## Modo SQL avanzado

Activa **Modo SQL avanzado** para escribir tu propia fórmula DuckDB para el campo `valor`
(el plugin añade automáticamente el `GROUP BY` geográfico). Ejemplos:

```sql
AVG(p26_edad)
100.0 * SUM(CASE WHEN p25_sexo = 1 THEN 1 END) / COUNT(*)
```

Funciona en los tres niveles, también con las tablas de fichas:

```sql
100.0 * SUM(tic_internet) / NULLIF(SUM(tic_total), 0)
```

## Desde Processing

El plugin registra dos algoritmos en la caja de herramientas, para modelos gráficos y
procesamiento por lotes:

- **Calcular indicador censal** — por departamento o municipio, cualquier censo.
- **Indicador por manzano/comunidad (CPV-2024)** — para un municipio y un área.

Las capas de Processing salen sin simbología y sin la capa de contexto (aplícalas en QGIS); el
panel sí hace las dos cosas. El algoritmo de manzanos produce **una** geometría por ejecución, así
que el área no es opcional.

## Caché en disco

El plugin guarda en `~/.censosbo_qgis/`: los diccionarios por año, las geometrías de
manzanos y comunidades (`fichas/`) y las capas generadas (`capas/`). Los microdatos **no** se
descargan: DuckDB los consulta en remoto. Si se publican datos nuevos en los releases, borra la
carpeta para forzar una recarga.
