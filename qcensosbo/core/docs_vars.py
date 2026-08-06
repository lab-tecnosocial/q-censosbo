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
import re
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


def _fila_y_tabla(anio, variable, tabla=None):
    """(fila, nombre de tabla del paquete R) de una variable, o (None, None).

    Prueba los nombres de tabla del paquete R que corresponden a la tabla del
    plugin; si no acierta con ninguno, busca la variable en cualquier tabla de ese
    año, porque unas pocas están documentadas bajo una tabla distinta de la que se
    consulta (`discapacidad`, por ejemplo).

    La tabla de origen se devuelve porque los saltos del cuestionario solo se
    comparan entre preguntas de la misma tabla: la pregunta 15 de vivienda no
    condiciona a la 16 de persona.
    """
    if not variable:
        return None, None
    datos = _cargar()
    if not datos:
        return None, None

    for tabla_r in _TABLAS.get(tabla, ()):
        fila = datos.get((anio, tabla_r, variable))
        if fila:
            return fila, tabla_r
    for (a, t, v), fila in datos.items():
        if a == anio and v == variable:
            return fila, t
    return None, None


def documentacion(anio, variable, tabla=None):
    """Fila de documentación de una variable, o `None` si no está."""
    return _fila_y_tabla(anio, variable, tabla)[0]


# ── Los saltos del cuestionario ─────────────────────────────────────────────
#
# El campo `universo_literal` es fiel al DDI del INE, y el DDI declara ahí solo el
# filtro grueso: «Solo para personas de 7 años o más de edad». El resto del
# universo —a quién llegó de verdad la pregunta— está en OTRO campo del mismo
# metadato y en prosa: el texto de la pregunta anterior, con su instrucción de
# salto («1 Sí (PASE A P49)»).
#
# Por eso los «Sin dato» parecen inexplicables. En `p45_agro` de Santiváñez son
# 3.531, y solo 851 son menores de 7 años: los otros 2.680 están DENTRO del
# universo declarado y la pregunta no les llegó porque el cuestionario la salta
# (la 45 solo se hace a quien respondió «no» en la 43 y en la 44). Ese salto no se
# puede filtrar por edad, y sin decirlo el aviso de universo miente por omisión.
#
# **Lo que esto NO hace:** reconstruir el flujo entero del cuestionario. El DDI
# solo declara con «(PASE A Pn)» una parte de los condicionamientos; los
# subcampos («anote el país solo si respondió "otro país"») y las convergencias de
# ramas —la p49 la contesta quien llegó por cualquiera de tres caminos— viven en
# prosa y no se derivan.
#
# De ahí la regla de la casa: esto **explica** los «Sin dato» cuando el metadato lo
# permite, y no se usa NUNCA para calcular. El denominador sigue saliendo de los
# nulos (`COUNT(columna)` en query_engine), que es el universo efectivo tal como lo
# aplicó el operativo y coincide con REDATAM sin depender de ningún parseo. Si se
# invirtieran los papeles, un cambio de redacción en el catálogo del INE movería
# una cifra publicada. `scripts/qa_redatam.py` fija las dos mitades.

# «1 Sí ( PASE A P49)» → código 1, respuesta «Sí», destino 49. El paréntesis llega
# con y sin espacio, y el destino con y sin la «P» («PASE A 17» en vivienda).
_PATRON_SALTO = re.compile(
    r"(\d+)\s*([^\d()]*?)\s*\(\s*PASE\s+A\s*P?\.?\s*(\d+)\s*\)", re.IGNORECASE)

# «43. La semana pasada, ¿trabajó…» → 43.
_PATRON_NUM = re.compile(r"^\s*(\d+)\s*[.)]")

# Una etiqueta de respuesta es «Sí», «No», «No tiene». Cuando lo capturado es en
# realidad la subpregunta siguiente («¿La mayor parte para la venta?»), no se
# inventa nada: se cita el código. Un aviso con el código es pobre; uno con el
# texto equivocado es falso.
_MAX_ETIQUETA = 40

_saltos_cache = None


def _numero_pregunta(fila):
    """Número de la pregunta que da origen a la variable, o None."""
    m = _PATRON_NUM.match((fila.get("pregunta_literal") or ""))
    return int(m.group(1)) if m else None


def _etiqueta_corta(texto, max_len=90):
    """El enunciado de la pregunta, sin el número y sin la lista de opciones."""
    texto = " ".join((texto or "").split())
    texto = _PATRON_NUM.sub("", texto).strip()
    # El enunciado acaba en «?» (interrogativa) o en «:» (enumerativa); lo que
    # sigue son las opciones, que no caben en un aviso.
    corte = min((p for p in (texto.find("?"), texto.find(":")) if p > 0),
                default=-1)
    if corte > 0:
        texto = texto[:corte + 1] if texto[corte] == "?" else texto[:corte]
    texto = texto.strip(" .,;:")
    return texto if len(texto) <= max_len else texto[:max_len].rsplit(" ", 1)[0] + "…"


def _saltos():
    """{(anio, tabla_r): {num_pregunta: {"texto", "opciones": [(cod, etq, destino)]}}}

    Se construye una vez por sesión sobre el CSV ya cargado: no hay red ni DuckDB
    de por medio, así que está disponible al instante.
    """
    global _saltos_cache
    if _saltos_cache is not None:
        return _saltos_cache

    saltos = {}
    for (anio, tabla_r, _), fila in _cargar().items():
        texto = fila.get("pregunta_literal") or ""
        num = _numero_pregunta(fila)
        if num is None:
            continue
        encontrados = _PATRON_SALTO.findall(texto)
        if not encontrados:
            continue
        preguntas = saltos.setdefault((anio, tabla_r), {})
        entrada = preguntas.setdefault(
            num, {"texto": _etiqueta_corta(texto), "opciones": []})
        for codigo, etiqueta, destino in encontrados:
            etiqueta = etiqueta.strip(" .,;:")
            if "¿" in etiqueta or len(etiqueta) > _MAX_ETIQUETA:
                etiqueta = ""          # se citará el código
            opcion = (int(codigo), etiqueta, int(destino))
            # La misma pregunta son varias columnas (la p36 son seis) y declara el
            # mismo salto en todas: se agrupa por pregunta, no por variable.
            if opcion not in entrada["opciones"]:
                entrada["opciones"].append(opcion)
    _saltos_cache = saltos
    return saltos


def condiciones_previas(anio, variable, tabla=None):
    """Preguntas anteriores que dejan fuera a parte del universo declarado.

    Una pregunta `k` está condicionada por toda pregunta `j < k` cuyo salto lleva a
    un destino **posterior** a `k`: quien disparó ese salto nunca llegó a la `k`.
    Solo se recogen los códigos que saltan por encima de `k`; los que caen antes no
    condicionan nada (la p47 salta al 48 con un código y al 49 con otro, y para la
    p48 solo cuenta el segundo).

    Devuelve una lista de dicts con `num`, `pregunta` y `respuestas` —[(código,
    etiqueta)]—, ordenada por número de pregunta. Lista vacía es un resultado
    normal: la mayoría de las preguntas no están condicionadas.
    """
    fila, tabla_r = _fila_y_tabla(anio, variable, tabla)
    if not fila:
        return []
    num = _numero_pregunta(fila)
    if num is None:
        return []

    salida = []
    for j, entrada in sorted(_saltos().get((anio, tabla_r), {}).items()):
        if j >= num:
            continue
        respuestas = [(cod, etq) for cod, etq, destino in entrada["opciones"]
                      if destino > num]
        if respuestas:
            salida.append({"num": j, "pregunta": entrada["texto"],
                           "respuestas": respuestas})
    return salida


def frase_condiciones(anio, variable, tabla=None):
    """Las condiciones previas como una frase cerrada, o None si no hay ninguna.

    Una sola definición para el aviso del resultado y para el tooltip: si cada
    sitio la redactara a su manera, la misma variable se explicaría de dos formas.

    Se redacta en negativo —«no a quienes respondieron "Sí"»— y no en positivo,
    porque el complemento no es «quienes respondieron No»: la no respuesta
    declarada del censo («Sin especificar») también llega a la pregunta siguiente.
    En la p45 de Santiváñez son 137 casos, y decir «solo a quienes dijeron No» los
    borraría.
    """
    condiciones = condiciones_previas(anio, variable, tabla)
    if not condiciones:
        return None

    clausulas = []
    for c in condiciones:
        etiquetas = [e for _, e in c["respuestas"] if e]
        if etiquetas:
            respuesta = " o ".join(f"«{e}»" for e in etiquetas)
        else:
            respuesta = " o ".join(f"el código {cod}" for cod, _ in c["respuestas"])
        clausulas.append(f"{respuesta} en la pregunta {c['num']} "
                         f"(«{c['pregunta']}»)")
    return (f"no a quienes respondieron {' ni '.join(clausulas)}: el cuestionario "
            f"les salta esta pregunta")


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

    # El universo del DDI no incluye los saltos del cuestionario, y son la mitad
    # de la respuesta: sin ellos, «personas de 7 años o más» deja creer que a las
    # demás se les preguntó.
    saltos = frase_condiciones(anio, variable, tabla)
    if saltos:
        prefijo = "Y, dentro de ese universo, " if universo else "Pero "
        partes.append(prefijo + saltos + ".")

    regla = (fila.get("regla_derivacion") or "").strip()
    if regla:
        if len(regla) > max_definicion:
            regla = regla[:max_definicion].rsplit(" ", 1)[0] + "…"
        partes.append(f"Cómo se construyó: {regla}")

    return "\n\n".join(partes)
