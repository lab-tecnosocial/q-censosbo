"""
Documentación conceptual del INE para las variables del censo.

Lo que el diccionario no dice: qué mide exactamente la variable, la pregunta tal
como se leyó en campo, el universo redactado en palabras y —para las derivadas— la
regla con que el INE o REDATAM la calcularon. Sale de los diccionarios DDI del
catálogo ANDA del INE, vía `censosbo::codebook_docs_meta`.

Va empaquetada como CSV (`data/docs_variables.csv`, generado por
`scripts/build_docs_vars.R`) porque **no se publica en los GitHub Releases**: solo
existe como dataset del paquete R. Igual que el catálogo de fichas, se lee con la
librería estándar, sin red ni DuckDB, así que está disponible al instante.

Se usa para el tooltip de la variable: son textos largos —hasta 1.982 caracteres la
definición— que no caben en el panel, pero que a demanda valen mucho para decidir
si una variable mide lo que uno cree.
"""

import csv
from pathlib import Path

from . import log

DOCS_PATH = Path(__file__).parent.parent / "data" / "docs_variables.csv"

# El CSV usa los nombres de tabla del paquete R; el plugin, los suyos. Varios
# nombres por tabla porque cambian entre censos (1976 usa `poblacion`).
_TABLAS = {
    "personas":   ("persona", "poblacion", "discapacidad"),
    "viviendas":  ("vivienda",),
    "emigracion": ("emigracion",),
    "mortalidad": ("mortalidad",),
}

_cache = None


def _cargar():
    """{(anio, tabla_r, variable): fila} — leído una vez por sesión."""
    global _cache
    if _cache is not None:
        return _cache
    datos = {}
    try:
        with open(DOCS_PATH, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                try:
                    anio = int(r["anio"])
                except (KeyError, TypeError, ValueError):
                    continue
                datos[(anio, r.get("tabla", ""), r.get("variable", ""))] = r
    except OSError as exc:
        # Sin el CSV el plugin funciona igual: solo no hay tooltip enriquecido.
        log.aviso(f"No se pudo leer {DOCS_PATH.name}", exc)
        datos = {}
    _cache = datos
    return _cache


def documentacion(anio, variable, tabla=None):
    """Fila de documentación de una variable, o `None` si no está.

    Prueba los nombres de tabla del paquete R que corresponden a la tabla del
    plugin; si no acierta con ninguno, busca la variable en cualquier tabla de ese
    año, porque unas pocas están documentadas bajo una tabla distinta de la que se
    consulta (`discapacidad`, por ejemplo).
    """
    if not variable:
        return None
    datos = _cargar()
    if not datos:
        return None

    for tabla_r in _TABLAS.get(tabla, ()):
        fila = datos.get((anio, tabla_r, variable))
        if fila:
            return fila
    for (a, _, v), fila in datos.items():
        if a == anio and v == variable:
            return fila
    return None


def texto_ayuda(anio, variable, tabla=None, max_definicion=600):
    """Texto listo para un tooltip, o `""` si la variable no está documentada.

    Se recorta la definición porque alguna llega a 1.982 caracteres y un tooltip de
    ese tamaño tapa el panel entero.
    """
    fila = documentacion(anio, variable, tabla)
    if not fila:
        return ""

    partes = []
    definicion = (fila.get("definicion") or "").strip()
    if definicion:
        if len(definicion) > max_definicion:
            definicion = definicion[:max_definicion].rsplit(" ", 1)[0] + "…"
        partes.append(definicion)

    pregunta = (fila.get("pregunta_literal") or "").strip()
    if pregunta:
        partes.append(f"Pregunta en campo: «{pregunta}»")

    universo = (fila.get("universo_literal") or "").strip()
    if universo:
        partes.append(f"Se aplicó a: {universo}")

    regla = (fila.get("regla_derivacion") or "").strip()
    if regla:
        if len(regla) > max_definicion:
            regla = regla[:max_definicion].rsplit(" ", 1)[0] + "…"
        partes.append(f"Cómo se construyó: {regla}")

    return "\n\n".join(partes)
