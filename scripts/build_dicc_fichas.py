#!/usr/bin/env python3
"""
Genera `qcensosbo/data/dicc_fichas.csv`: el diccionario de indicadores de las
fichas por manzano y comunidad del CPV-2024.

**Fuente: el diccionario publicado en el release**
(`data-fichas-v1.0.0/diccionario_fichas_v1.parquet`, desde censosbo 1.5.0). Antes
se leía `data-raw/fichas/campos.csv` del repo del paquete R, que es material de
construcción interno: no está versionado como artefacto, puede cambiar de forma sin
aviso y obligaba a replicar aquí el denominador y el orden de los bloques. El
diccionario publicado ya trae `denominador`, `bloque_orden`, `bloque_etiqueta` y
`tema`, así que esa lógica duplicada desaparece.

¿Y por qué un CSV empaquetado y no leerlo en remoto cada vez? Para que el selector
de variables se llene al instante y sin red, que es lo que se nota al abrir el
panel. Las 208 filas pesan poco.

Lo que este script **añade** al diccionario publicado, porque el plugin lo necesita
y el parquet no lo trae:

  - `expr`  — expresión del numerador. Para las variables de la ficha es la columna
              misma; para las derivadas, la suma del par hombres+mujeres.
  - Las **variables de ambos sexos**: por cada par `_h`/`_m`, su suma. Es lo que
    casi siempre se quiere mapear («% de población de 60 o más años», no
    «…, hombres») y la ficha no lo trae calculado.
  - Las tres columnas de la tabla `unidad` que sí son indicadores, con la expresión
    especial de `ficha` (un lógico que hay que contar).

Uso:
    uv run --with duckdb --with pandas python scripts/build_dicc_fichas.py

Re-ejecutar cuando censosbo republique el diccionario de fichas (p. ej. si el INE
amplía la ficha resumen). Al terminar, el script compara con el CSV anterior y
avisa de lo que cambió, para que la diferencia se revise antes de commitear.
"""

import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "qcensosbo" / "data" / "dicc_fichas.csv"

FUENTE = ("https://github.com/lab-tecnosocial/censosbo/releases/download/"
          "data-fichas-v1.0.0/diccionario_fichas_v1.parquet")

# El diccionario nombra las tablas en singular; el plugin, en plural.
TABLAS = {"ficha": "fichas", "unidad": "unidades"}

# Columnas de identificación y geografía: son filtros o claves, no algo que se
# mapee, así que no entran en el selector de variables.
NO_INDICADORES = {"codigo", "nombre", "area", "idep", "iprov", "imun"}

# `ficha` es un lógico (si el INE liberó la ficha de esa unidad), así que su
# indicador es contar los verdaderos sobre el total de unidades del grupo.
EXPR_ESPECIALES = {
    "ficha": ("CASE WHEN ficha THEN 1 ELSE 0 END", "1"),
}

CAMPOS_OUT = ["tabla", "variable", "etiqueta", "bloque", "bloque_etiqueta",
              "tipo", "expr", "denominador", "tema", "tema_etiqueta"]


def _leer_fuente():
    import duckdb

    con = duckdb.connect()
    con.execute("INSTALL httpfs; LOAD httpfs;")
    filas = con.execute(f"SELECT * FROM read_parquet('{FUENTE}')").df()
    return filas.to_dict("records")


def _texto(valor):
    """Valor de texto limpio; los nulos de parquet llegan como NaN."""
    if valor is None or valor != valor:                  # NaN != NaN
        return ""
    return str(valor).strip()


def _fila(origen, variable=None, etiqueta=None, expr=None, denominador=None):
    """Fila de salida, tomando del diccionario todo lo que no se sobrescriba."""
    var = variable or _texto(origen["variable"])
    expr_esp, den_esp = EXPR_ESPECIALES.get(var, (None, None))
    return {
        "tabla":           TABLAS.get(_texto(origen["tabla"]), _texto(origen["tabla"])),
        "variable":        var,
        "etiqueta":        etiqueta if etiqueta is not None else _texto(origen["etiqueta"]),
        "bloque":          _texto(origen["bloque"]),
        "bloque_etiqueta": _texto(origen["bloque_etiqueta"]),
        "tipo":            _texto(origen["tipo"]) or "numerica",
        "expr":            expr or expr_esp or var,
        "denominador":     (denominador if denominador is not None
                            else den_esp if den_esp is not None
                            else _texto(origen["denominador"])),
        "tema":            _texto(origen["tema"]),
        "tema_etiqueta":   _texto(origen["tema_etiqueta"]),
    }


def _derivadas(origenes):
    """Variables de ambos sexos: por cada par _h/_m, la suma de los dos.

    El denominador sale del que el diccionario declara para la rama `_h`
    (`pob_total_h`), convertido también en suma: `(pob_total_h + pob_total_m)`.
    """
    por_base = {}
    for o in origenes:
        var = _texto(o["variable"])
        if not (var.endswith("_h") or var.endswith("_m")):
            continue
        por_base.setdefault(var[:-2], {})[var[-1]] = o

    filas = []
    for base, par in sorted(por_base.items()):
        if set(par) != {"h", "m"}:
            continue                                  # par incompleto: no derivar
        # La etiqueta del par viene como "…, hombres": se corta el sufijo de sexo.
        etiqueta = _texto(par["h"]["etiqueta"]).rsplit(",", 1)[0].strip()
        den = _texto(par["h"]["denominador"])
        if den.endswith("_h"):
            den = f"({den} + {den[:-2]}_m)"
        filas.append(_fila(par["h"], variable=base, etiqueta=etiqueta,
                           expr=f"({base}_h + {base}_m)", denominador=den))
    return filas


def construir():
    origenes = [o for o in _leer_fuente()
                if _texto(o["variable"]) not in NO_INDICADORES]
    if len(origenes) < 190:
        raise SystemExit(
            f"El diccionario publicado trajo solo {len(origenes)} indicadores. "
            f"Revisa {FUENTE} antes de continuar.")

    filas = [_fila(o) for o in origenes]
    filas.extend(_derivadas(origenes))

    # Orden del selector: por bloque (el orden que declara el diccionario) y dentro
    # de cada bloque la variable de ambos sexos antes de sus dos desagregaciones.
    orden_bloque = {}
    for o in origenes:
        bloque = _texto(o["bloque"])
        valor = o.get("bloque_orden")
        if bloque and bloque not in orden_bloque:
            orden_bloque[bloque] = int(valor) if valor == valor else 99

    def clave(f):
        var = f["variable"]
        base = var[:-2] if var.endswith(("_h", "_m")) else var
        sufijo = {"_h": 1, "_m": 2}.get(var[-2:], 0)
        return (orden_bloque.get(f["bloque"], 99), f["tabla"], base, sufijo)

    filas.sort(key=clave)
    return filas


def _comparar_con_anterior(filas):
    """Avisa de lo que cambió respecto al CSV commiteado, para revisarlo."""
    if not OUT.exists():
        print("  (no había CSV anterior)")
        return
    with open(OUT, encoding="utf-8") as f:
        previas = list(csv.DictReader(f))
    antes = {(r["tabla"], r["variable"]) for r in previas}
    ahora = {(r["tabla"], r["variable"]) for r in filas}
    print(f"  antes {len(previas)} filas → ahora {len(filas)}")
    for etiqueta, conjunto in (("solo en el anterior", antes - ahora),
                               ("nuevas", ahora - antes)):
        if conjunto:
            muestra = sorted(v for _, v in conjunto)[:10]
            print(f"  ⚠ {len(conjunto)} {etiqueta}: {muestra}")
    prev_por_var = {(r["tabla"], r["variable"]): r for r in previas}
    cambios = [
        k for k in antes & ahora
        for nueva in [next(r for r in filas if (r["tabla"], r["variable"]) == k)]
        if (prev_por_var[k].get("expr") != nueva["expr"]
            or prev_por_var[k].get("denominador") != nueva["denominador"])
    ]
    if cambios:
        print(f"  ⚠ {len(cambios)} con expr/denominador distinto: "
              f"{sorted(v for _, v in cambios)[:10]}")
    else:
        print("  ✓ expr y denominador idénticos en todas las variables comunes")


def main():
    filas = construir()
    print(f"Diccionario de fichas desde el release: {len(filas)} filas")
    _comparar_con_anterior(filas)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CAMPOS_OUT)
        w.writeheader()
        w.writerows(filas)
    print(f"✓ {OUT}")


if __name__ == "__main__":
    main()
