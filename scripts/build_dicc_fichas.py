#!/usr/bin/env python3
"""
Genera `qcensosbo/data/dicc_fichas.csv`: el diccionario de indicadores de las
fichas por manzano y comunidad del CPV-2024.

¿Por qué un CSV empaquetado y no un parquet remoto? El release de datos
(`data-fichas-v1.0.0`) publica `unidad.parquet`, `ficha.parquet` y las
geometrías, pero NO un diccionario: las etiquetas de los 194 indicadores solo
existen en el repo del paquete R (`data-raw/fichas/campos.csv` y
`campos_vivienda.csv`), desde donde se inyectan en su `codebook_meta.rda`. Este
script las trae de ahí y las deja como CSV dentro del plugin, así el combo de
variables se llena al instante y sin red.

Además del nombre y la etiqueta, el CSV lleva dos columnas que el plugin usa
para construir SQL sin lógica ad-hoc dispersa:

  - `expr`         — expresión del numerador (la columna, o la suma de un par
                     hombres+mujeres en las variables derivadas).
  - `denominador`  — expresión del total del bloque, que convierte el conteo en
                     porcentaje. Vacío cuando la variable ES un total.

Uso:
    python scripts/build_dicc_fichas.py

Re-ejecutar si el INE amplía la ficha resumen (o si cambian las etiquetas en el
paquete R). Solo usa la librería estándar.
"""

import csv
import io
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "qcensosbo" / "data" / "dicc_fichas.csv"

RAW = "https://raw.githubusercontent.com/lab-tecnosocial/censosbo/HEAD/data-raw/fichas"
FUENTES = ("campos.csv", "campos_vivienda.csv")

# Prefijo de variable de cada bloque cuyas variables van desagregadas por sexo
# (sufijos _h/_m). El total del bloque es "<prefijo>_total_<sufijo>".
BLOQUES_SEXO = {
    "poblacion":     "pob",
    "educacion":     "edu",
    "salud_lugar":   "salud_lugar",
    "salud_seguro":  "salud_seguro",
    "nacimiento":    "nac",
    "residencia":    "res",
    "ocupacion":     "ocup",
    "actividad":     "act",
}

# Orden en que se ofrecen los bloques en el selector de variables.
ORDEN_BLOQUES = [
    "poblacion", "educacion", "salud_lugar", "salud_seguro", "nacimiento",
    "residencia", "ocupacion", "actividad", "vivienda", "servicios", "tic",
    "material", "hacinamiento", "hogar",
]

# Los bloques de la ficha ampliada de vivienda cuentan viviendas particulares
# con personas presentes, no el total de viviendas (ver ?get_fichas_2024).
BASE_VIVIENDA_PRESENTES = {"material", "hacinamiento", "hogar"}


def _leer_remoto(nombre):
    url = f"{RAW}/{nombre}"
    req = urllib.request.Request(url, headers={"User-Agent": "q-censosbo-build/1.0"})
    with urllib.request.urlopen(req) as resp:
        texto = resp.read().decode("utf-8")
    return list(csv.DictReader(io.StringIO(texto)))


def _denominador(bloque, variable):
    """Total del bloque que sirve de denominador, o "" si la variable es un total."""
    if "_total" in variable:
        return ""

    if bloque in BLOQUES_SEXO:
        sufijo = variable.rsplit("_", 1)[-1]        # h | m
        return f"{BLOQUES_SEXO[bloque]}_total_{sufijo}"

    if bloque == "servicios":
        # serv_agua_caneria → serv_agua_total
        sub = variable.split("_")[1]
        return f"serv_{sub}_total"

    if bloque == "vivienda":
        # El denominador de la tenencia es su propio total; el del tipo y la
        # condición de ocupación es el total de viviendas.
        return "viv_tenencia_total" if variable.startswith("viv_tenencia_") else "viv_total"

    if bloque == "tic":
        return "tic_total"

    if bloque in BASE_VIVIENDA_PRESENTES:
        return "viv_tipo_presentes"

    return ""


def _fila(tabla, variable, etiqueta, bloque, expr="", denominador=""):
    return {
        "tabla": tabla,
        "variable": variable,
        "etiqueta": etiqueta,
        "bloque": bloque,
        "tipo": "numerica",
        "expr": expr or variable,
        "denominador": denominador,
    }


def _derivadas(campos):
    """Variables de ambos sexos: por cada par _h/_m, la suma de los dos.

    Es lo que casi siempre se quiere mapear ("% de población de 60 o más años",
    no "…, hombres"), y en la ficha no viene dada: hay que sumarla. Se declara
    aquí como expresión para que el motor no necesite saber nada de sexos.
    """
    por_base = {}
    for c in campos:
        var = c["variable"]
        if not (var.endswith("_h") or var.endswith("_m")):
            continue
        base = var[:-2]
        por_base.setdefault(base, {})[var[-1]] = c

    filas = []
    for base, par in por_base.items():
        if set(par) != {"h", "m"}:
            continue                                  # par incompleto: no derivar
        bloque = par["h"]["bloque"]
        etiqueta = par["h"]["etiqueta"].rsplit(",", 1)[0].strip()
        expr = f"({base}_h + {base}_m)"
        den = _denominador(bloque, f"{base}_h")
        if den:
            den_base = den[:-2]                       # …_total_h → …_total
            den = f"({den_base}_h + {den_base}_m)"
        filas.append(_fila("fichas", base, etiqueta, bloque, expr, den))
    return filas


def construir():
    campos = []
    for nombre in FUENTES:
        campos.extend(_leer_remoto(nombre))
    if len(campos) != 194:
        raise SystemExit(
            f"Se esperaban 194 indicadores en el paquete R, llegaron {len(campos)}. "
            "Revisa data-raw/fichas/ en lab-tecnosocial/censosbo antes de continuar."
        )

    filas = [
        _fila("fichas", c["variable"], c["etiqueta"], c["bloque"],
              denominador=_denominador(c["bloque"], c["variable"]))
        for c in campos
    ]
    filas.extend(_derivadas(campos))

    # Tabla `unidad`: el universo completo de unidades censales. Solo se ofrecen
    # las tres columnas que son indicadores; el resto (codigo, nombre, area,
    # geografía) son identificadores o filtros, no algo que se mapee.
    filas.extend([
        _fila("unidades", "personas", "Personas empadronadas en la unidad", "unidad"),
        _fila("unidades", "viviendas", "Viviendas empadronadas en la unidad", "unidad"),
        # SUM(1) es COUNT(*): el porcentaje sale sobre las unidades del grupo.
        _fila("unidades", "ficha", "Unidades con ficha de indicadores liberada",
              "unidad", expr="CASE WHEN ficha THEN 1 ELSE 0 END", denominador="1"),
    ])

    # Orden del selector: bloque temático, y dentro de cada bloque la variable de
    # ambos sexos antes de sus dos desagregaciones.
    orden = {b: i for i, b in enumerate(ORDEN_BLOQUES)}

    def clave(f):
        var = f["variable"]
        base = var[:-2] if var.endswith(("_h", "_m")) else var
        sufijo = {"_h": 1, "_m": 2}.get(var[-2:], 0)
        return (orden.get(f["bloque"], 99), base, sufijo)

    filas.sort(key=clave)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    campos_out = ["tabla", "variable", "etiqueta", "bloque", "tipo", "expr", "denominador"]
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=campos_out)
        w.writeheader()
        w.writerows(filas)

    n_der = sum(1 for f in filas if f["expr"] != f["variable"] and f["tabla"] == "fichas")
    print(f"✓ {OUT.relative_to(ROOT)}  ({len(filas)} filas: "
          f"{len(campos)} de la ficha + {n_der} derivadas + 3 de unidades)")


if __name__ == "__main__":
    construir()
