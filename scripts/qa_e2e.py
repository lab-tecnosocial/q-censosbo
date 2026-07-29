#!/usr/bin/env python3
"""
Prueba end-to-end REAL: motor DuckDB y capa QGIS de verdad, sin dobles.

Complementa a `qa_headless.py`, que ejercita el panel con workers sincrónicos y
datos del fixture. Aquí no se sustituye nada: se consulta el Parquet remoto con
DuckDB y se construye la capa con QGIS.

Está pensada para correr **dentro del contenedor de QGIS 4** (ver
`scripts/qa_versiones.sh`), por dos razones:

  - En macOS el `_duckdb.so` del Python de QGIS está firmado para el bundle y el
    sistema lo rechaza al importarlo fuera de él, así que este camino no se podía
    probar en local. En Linux no hay tal restricción.
  - Es la única forma de comprobar de verdad la compatibilidad con Qt6: que el
    plugin *importe* en PyQt6 no garantiza que el flujo completo funcione.

Cuando existe `dist/qa_fixture.pkl` (capturado con Qt5, ver `qa_fixture.py`), los
resultados se comparan contra él: así se detecta cualquier divergencia numérica
entre las dos ramas de Qt, no solo que el código no reviente.

Uso:
    scripts/qa_versiones.sh e2e          # forma normal
    python3 scripts/qa_e2e.py        # si ya estás dentro de un QGIS con duckdb

Requiere internet: consulta los releases del paquete censosbo.
"""

import os
import pickle
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "dist" / "qa_fixture.pkl"

_ok = 0
_fallos = []


def check(cond, msg):
    global _ok
    if cond:
        _ok += 1
        print(f"  ✓ {msg}")
    else:
        _fallos.append(msg)
        print(f"  ✗ {msg}")


def titulo(t):
    print(f"\n{'=' * 70}\n{t}\n{'=' * 70}")


def main():
    from qgis.core import Qgis, QgsApplication

    titulo(f"Entorno: QGIS {Qgis.QGIS_VERSION}")
    from qgis.PyQt.QtCore import QT_VERSION_STR
    print(f"  Qt {QT_VERSION_STR} · Python {sys.version.split()[0]}")

    QgsApplication.setPrefixPath("/usr", True)
    app = QgsApplication([], False)
    app.initQgis()

    from qcensosbo.core.query_engine import duckdb_available, get_parquet_urls
    from qcensosbo.core.aggregator import agregar_datos
    from qcensosbo.core import layer_builder

    fx = None
    if FIXTURE.exists():
        with open(FIXTURE, "rb") as f:
            fx = pickle.load(f)

    titulo("A · El motor de consulta arranca")
    check(duckdb_available(), "DuckDB disponible dentro de QGIS")
    urls = get_parquet_urls(2024, "personas")
    check(len(urls) == 9, f"2024/personas son 9 parquet por departamento ({len(urls)})")
    check(all(u.startswith("https://") for u in urls), "todas las URLs son https")

    titulo("B · Agregación real sobre el Parquet remoto")
    df = agregar_datos(urls, "departamento", "__count__", "__count__")
    check(len(df) == 9, f"devuelve los 9 departamentos ({len(df)})")
    check(set(df.columns) >= {"geo_code", "geo_nombre", "valor"},
          "con las columnas geo_code, geo_nombre y valor")
    check(df["valor"].notna().all(), "sin valores nulos")
    total = int(df["valor"].sum())
    print(f"    total nacional: {total:,}".replace(",", "."))

    if fx is not None:
        clave = (2024, "personas", "departamento", "__count__", "__count__",
                 None, None, None, None, None)
        esperado = fx["agg"].get(clave)
        if esperado is not None:
            previo = {str(r["geo_code"]): int(r["valor"])
                      for _, r in esperado.iterrows()}
            ahora = {str(r["geo_code"]): int(r["valor"]) for _, r in df.iterrows()}
            check(previo == ahora,
                  "coincide exactamente con el fixture capturado en Qt5")
        else:
            print("    (el fixture no trae este escenario; se omite el contraste)")
    else:
        print("    (sin dist/qa_fixture.pkl: no hay contraste con Qt5)")

    titulo("C · La capa se construye y se estiliza")
    capa = layer_builder.crear_capa(df, "departamento", "qa e2e")
    check(capa.isValid(), "la capa es válida")
    check(capa.featureCount() == 9, f"tiene 9 features ({capa.featureCount()})")
    check(type(capa.renderer()).__name__ == "QgsGraduatedSymbolRenderer",
          "lleva renderer graduado")
    rangos = capa.renderer().ranges()
    check(len(rangos) > 1, f"con más de una clase ({len(rangos)})")
    check(all(r.label() for r in rangos), "y todas las clases tienen etiqueta")

    valores = [f["valor_censo"] for f in capa.getFeatures()]
    check(sum(valores) == total, "los valores de la capa suman igual que la consulta")

    titulo("D · Tipado de campos en esta rama de Qt")
    from qcensosbo.core.compat import TIPO_DECIMAL, TIPO_TEXTO
    campo = capa.fields().field("valor_censo")
    check(campo.isNumeric(), "valor_censo es numérico")
    print(f"    TIPO_TEXTO={TIPO_TEXTO!r} TIPO_DECIMAL={TIPO_DECIMAL!r}")

    print(f"\n{'=' * 70}")
    print(f"{_ok} comprobaciones correctas, {len(_fallos)} fallos")
    for f in _fallos:
        print(f"  ✗ {f}")
    print(f"{'=' * 70}")

    sys.stdout.flush()
    # os._exit: duckdb aborta en el apagado del intérprete dentro de QGIS.
    os._exit(1 if _fallos else 0)


if __name__ == "__main__":
    main()
