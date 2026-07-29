"""
El universo de vivienda: qué registros de la tabla de viviendas son viviendas.

La tabla de viviendas del censo NO contiene solo viviendas. Su variable de tipo
de vivienda incluye categorías que no son unidades habitacionales, sino
situaciones de las personas censadas fuera de una vivienda: «persona que vive en
la calle» y «en tránsito» (terminal, aeropuerto, puerto). Esos registros existen
porque a esas personas hay que colgarlas de algún registro de la tabla, pero el
INE no las cuenta como viviendas en ningún tabulado.

En el CPV-2024 la diferencia está comprobada al registro:

    4.490.488  registros de la tabla (todos)
      - 3.311  v01_tipoviv = 15, persona que vive en la calle
      - 6.976  v01_tipoviv = 16, en tránsito
    = 4.480.201  viviendas   <- total oficial del INE

Y no solo en el total: agregando por municipio, restringir a los códigos 1-14
reproduce las viviendas del geoportal (tabla `unidades`) en los 343 municipios;
con el total crudo solo coincidían 20.

Este módulo es el espejo de `R/universos_vivienda.R` del paquete censosbo
(>= 1.7.0), donde `get_viviendas_2024()` aplica lo mismo por defecto. Las dos
implementaciones tienen que dar el mismo número: `scripts/qa_universos.py` lo
comprueba contra las cifras oficiales.
"""

# Por año: la columna de tipo de vivienda y los códigos que NO son vivienda.
#
# Cada censo nombra sus categorías de otra forma. 1976 no preguntó por calle ni
# tránsito, así que su lista está vacía y el universo oficial coincide con la
# tabla completa.
#
# `particulares` y `colectivas` se guardan para poder documentar y auditar el
# reparto; el plugin solo usa la exclusión.
TIPO_VIVIENDA = {
    2024: {"col": "v01_tipoviv", "no_vivienda": [15, 16],
           "particulares": list(range(1, 7)),   "colectivas": list(range(7, 15))},
    2012: {"col": "P01",         "no_vivienda": [7, 8],
           "particulares": list(range(1, 6)),   "colectivas": [6]},
    2001: {"col": "V04",         "no_vivienda": [24],
           "particulares": list(range(11, 16)), "colectivas": list(range(16, 24))},
    1992: {"col": "V01",         "no_vivienda": [13],
           "particulares": list(range(1, 7)),   "colectivas": list(range(7, 13))},
    1976: {"col": "v01",         "no_vivienda": [],
           "particulares": list(range(11, 18)), "colectivas": list(range(21, 28))},
}

# Tabla del plugin a la que aplica. En el plugin la tabla se llama "viviendas".
TABLA_VIVIENDA = "viviendas"

# Texto para el resumen y los metadatos de la capa. Se declara junto a los
# códigos para que no puedan desincronizarse.
COBERTURA_VIVIENDA = (
    "Las viviendas del universo oficial del INE. Quedan fuera los registros de "
    "personas censadas en la calle o en tránsito, que están en la misma tabla del "
    "censo pero no son viviendas."
)


def universo_sql(anio, tabla):
    """Condición SQL que define el universo de una tabla, o None si no aplica.

    Se aplica ANTES de cualquier filtro del usuario (ver `_filtered_from()` en
    query_engine). Devuelve None cuando la tabla no es la de viviendas o cuando el
    censo no tiene categorías que excluir (1976).
    """
    if tabla != TABLA_VIVIENDA:
        return None
    spec = TIPO_VIVIENDA.get(anio)
    if not spec or not spec["no_vivienda"]:
        return None
    codigos = ", ".join(str(c) for c in spec["no_vivienda"])
    # TRY_CAST porque unos censos guardan la columna como entero y otros como
    # texto; sin él, la comparación con enteros falla en silencio y devuelve todo.
    return f"TRY_CAST({spec['col']} AS INTEGER) NOT IN ({codigos})"


def cobertura_vivienda(anio, tabla):
    """Nota de cobertura para el resumen, o None si la tabla no la necesita."""
    if universo_sql(anio, tabla) is None:
        return None
    return COBERTURA_VIVIENDA
