"""
Puente entre Qt5 (QGIS 3) y Qt6 (QGIS 4) para lo poco que el plugin necesita.

El plugin declara `qgisMinimumVersion=3.28` y `qgisMaximumVersion=4.99`: tiene que
funcionar en las dos ramas. Casi todo el código es idéntico en ambas —los enums
cualificados (`Qt.AlignmentFlag.AlignCenter`) ya existen en PyQt5, así que se usan
directamente— salvo el **tipo de los campos** de una capa:

  - QGIS < 3.38 (Qt5): `QgsField(nombre, QVariant.String)`.
  - QGIS ≥ 3.38: se añadió el constructor con `QMetaType.Type.QString`, y el de
    `QVariant.Type` quedó obsoleto.
  - QGIS 4 (Qt6): `QVariant.String` **no existe** — PyQt6 dejó de exponer los tipos
    de QVariant como enum, así que el código antiguo revienta con AttributeError.

La discriminación es por versión de QGIS, no por `hasattr`: el atributo `QMetaType`
existe también en Qt5, pero el constructor de `QgsField` que lo acepta no llegó
hasta 3.38, y sip rechaza el tipo equivocado.

Uso:

    from .compat import TIPO_TEXTO, TIPO_DECIMAL
    fields.append(QgsField("codigo", TIPO_TEXTO))
"""

from qgis.core import Qgis

# QGIS 3.38 es la primera versión cuyo QgsField acepta QMetaType.Type.
_QMETATYPE_DESDE = 33800

if Qgis.QGIS_VERSION_INT >= _QMETATYPE_DESDE:
    from qgis.PyQt.QtCore import QMetaType

    TIPO_TEXTO = QMetaType.Type.QString
    TIPO_DECIMAL = QMetaType.Type.Double
    TIPO_ENTERO = QMetaType.Type.Int
else:
    from qgis.PyQt.QtCore import QVariant

    TIPO_TEXTO = QVariant.String
    TIPO_DECIMAL = QVariant.Double
    TIPO_ENTERO = QVariant.Int

__all__ = ["TIPO_TEXTO", "TIPO_DECIMAL", "TIPO_ENTERO"]
