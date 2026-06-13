# Uso

El panel se divide en **Datos**, **Análisis** y **Resumen del resultado**, y dos botones:
**`1 · Consultar`** y **`2 · Generar mapa`**.

## Flujo de trabajo

1. **Datos**
    - **Año** del censo (1976, 1992, 2001, 2012, 2024).
    - **Tabla** (Personas, Viviendas, …; varía según el año).
    - **Nivel**: Departamental o Municipal. *(El censo de 1976 solo está disponible a nivel
      departamental.)*
2. **Análisis**
    - **Variable**: se listan las columnas de la tabla con su descripción del diccionario
      oficial.
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
    - **Porcentaje de una categoría** — eliges una categoría y el mapa muestra su % por unidad.
- **Numérica** (p. ej. edad):
    - **Media**, **Mediana**, **Suma**, **Desviación estándar** y **Conteo**.
    - Puedes elegir el método de **clasificación** del mapa (Natural Breaks/Jenks, cuantiles,
      intervalo igual, desviación estándar).

## El resumen del resultado

Tras **Consultar** verás:

- **Qué se está mapeando** (una línea descriptiva del indicador).
- **Nacional**: el valor real a nivel país (p. ej. % nacional ponderado, edad media nacional).
- **La distribución** entre unidades: un *ranking* con barras (a nivel departamental) o un
  *histograma* por rangos (a nivel municipal).

## Modo SQL avanzado

Activa **Modo SQL avanzado** para escribir tu propia fórmula DuckDB para el campo `valor`
(el plugin añade automáticamente el `GROUP BY` geográfico). Ejemplos:

```sql
AVG(p26_edad)
100.0 * SUM(CASE WHEN p25_sexo = 1 THEN 1 END) / COUNT(*)
```

## Limpiar caché

El botón **Limpiar caché** borra los diccionarios y esquemas en memoria (y los descargados).
Útil si se publican datos nuevos en los releases y quieres forzar una recarga.
