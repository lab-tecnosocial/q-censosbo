"""
Registro de avisos del plugin en el panel de mensajes de QGIS.

Sirve a dos propósitos:

  - **Depurar.** El plugin envuelve en `try/except` los pasos opcionales (aplicar
    un estilo, leer una rampa de color, cargar el QSS) para que un fallo ahí no
    tumbe la operación entera. Antes esos fallos se descartaban con `pass` y no
    dejaban rastro, lo que complica diagnosticar problemas de campo — como el
    crash del repositorio propio de complementos, cuya causa nunca se confirmó.
  - **Pasar el escaneo de seguridad de plugins.qgis.org**, que marca
    `try/except/pass` (Bandit B110) como hallazgo bloqueante. Registrar el error
    en vez de silenciarlo resuelve el aviso arreglando el problema de fondo.

`core` se puede importar sin QGIS (lo aprovecha `scripts/qa_fixture.py`), así que
la disponibilidad de `qgis.core` se comprueba con `find_spec`, sin capturar
excepciones.
"""

import importlib.util

ETIQUETA = "Q-CensosBo"

_TIENE_QGIS = importlib.util.find_spec("qgis") is not None


def aviso(mensaje, exc=None):
    """Registra un aviso; añade la excepción si se pasa. Nunca lanza."""
    texto = f"{mensaje}: {exc!r}" if exc is not None else str(mensaje)
    if not _TIENE_QGIS:
        return
    from qgis.core import Qgis, QgsMessageLog
    QgsMessageLog.logMessage(texto, ETIQUETA, Qgis.MessageLevel.Warning)
