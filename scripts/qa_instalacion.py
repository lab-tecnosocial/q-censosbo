#!/usr/bin/env python3
"""
Comprueba que el ZIP publicable **instala y arranca**, como quien lo descarga.

Las otras pruebas corren sobre el árbol de trabajo; esta parte del artefacto real:
descomprime `dist/qcensosbo.zip` en un directorio limpio, lo pone en el `sys.path`
igual que hace el gestor de complementos y verifica lo que puede fallar solo al
empaquetar — un dato que no entró en el ZIP, una ruta que apuntaba al repo, un
módulo que no importa desde la copia instalada.

Uso:
    scripts/qa_versiones.sh instalacion
"""
import configparser, os, sys, zipfile, tempfile, shutil

destino = tempfile.mkdtemp(prefix="plugins_")
ZIP = os.environ.get("QA_ZIP", "/workspace/dist/qcensosbo.zip")
if not os.path.exists(ZIP):
    raise SystemExit(f"✗ Falta {ZIP}. Genéralo con: python scripts/build_release.py")
with zipfile.ZipFile(ZIP) as z:
    z.extractall(destino)
print("extraído en", destino, "->", os.listdir(destino))
sys.path.insert(0, destino)

# 1. metadata.txt tiene que ser parseable como lo hace el gestor de complementos
cfg = configparser.ConfigParser()
cfg.read(os.path.join(destino, "qcensosbo", "metadata.txt"), encoding="utf-8")
g = cfg["general"]
obligatorios = ["name", "qgisMinimumVersion", "description", "version", "author", "email"]
faltan = [c for c in obligatorios if not g.get(c)]
print("metadata: campos obligatorios faltantes:", faltan or "ninguno")
print("  version:", g["version"], "| min:", g["qgisMinimumVersion"],
      "| max:", g.get("qgisMaximumVersion"))
print("  changelog:", len(g.get("changelog", "").splitlines()), "líneas")

from qgis.core import QgsApplication
QgsApplication.setPrefixPath("/usr", True)
app = QgsApplication([], False)
app.initQgis()

# 2. Todos los módulos deben importar desde la copia instalada
import importlib
modulos = [
    "qcensosbo", "qcensosbo.censosbo_plugin", "qcensosbo.panel.dock_panel",
    "qcensosbo.core.aggregator", "qcensosbo.core.compat", "qcensosbo.core.data_loader",
    "qcensosbo.core.docs_vars", "qcensosbo.core.fichas", "qcensosbo.core.layer_builder",
    "qcensosbo.core.log", "qcensosbo.core.query_engine",
    "qcensosbo.processing.provider",
    "qcensosbo.processing.algorithms.calcular_indicador",
    "qcensosbo.processing.algorithms.indicador_manzanos",
]
for m in modulos:
    importlib.import_module(m)
print(f"módulos importados: {len(modulos)}/{len(modulos)}")

# 3. Los datos empaquetados se leen desde la ruta instalada, no la del repo
import qcensosbo.core.fichas as fichas
import qcensosbo.core.layer_builder as lb
import qcensosbo.core.docs_vars as dv
assert destino in str(fichas.DICC_PATH), fichas.DICC_PATH
print("catálogo de fichas:", len(fichas.catalogo("fichas")), "indicadores")
print("municipios:", len(lb.geo_nombres("municipio")),
      "| con superficie:", len(lb.geo_superficies("municipio")))
print("documentación:", "sí" if dv.texto_ayuda(2024, "p26_edad", "personas") else "NO")

# 4. classFactory: es lo que QGIS llama al activar el plugin
from qcensosbo import classFactory
print("classFactory existe:", callable(classFactory))

# 5. Los algoritmos de Processing se instancian y declaran sus parámetros
from qcensosbo.processing.algorithms.calcular_indicador import CalcularIndicadorAlgorithm
from qcensosbo.processing.algorithms.indicador_manzanos import IndicadorManzanosAlgorithm
for cls in (CalcularIndicadorAlgorithm, IndicadorManzanosAlgorithm):
    alg = cls(); alg.initAlgorithm()
    print(f"  {alg.name()}: {len(alg.parameterDefinitions())} parámetros, "
          f"ayuda {len(alg.shortHelpString())} chars")

shutil.rmtree(destino, ignore_errors=True)
print("\n✓ el ZIP instala y arranca")
sys.stdout.flush()
os._exit(0)
