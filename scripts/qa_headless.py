#!/usr/bin/env python3
"""
Fase 2 de la prueba headless: ejercita el panel y las capas dentro de QGIS, sin GUI.

Sustituye el motor DuckDB por el fixture de `qa_fixture.py` y ejecuta los workers
de forma síncrona, de modo que se puede afirmar sobre el estado real de los
widgets (qué se ve, qué opciones hay, qué dice el resumen) y sobre las capas y su
simbología. Cubre los casos que en su día fallaron, para que no vuelvan.

Uso (macOS; en Linux cambia la ruta del bundle):

    export PYTHONPATH=/Applications/QGIS.app/Contents/Resources/python3.12:\\
    /Applications/QGIS.app/Contents/Resources/python3.12/site-packages:\\
    /Applications/QGIS.app/Contents/Resources/python3.12/lib-dynload:\\
    /Applications/QGIS.app/Contents/Resources/qgis/python
    QT_QPA_PLATFORM=offscreen /Applications/QGIS.app/Contents/MacOS/python3.12 \\
        -u scripts/qa_headless.py

Sale con código 1 si alguna comprobación falla. Con `--shots DIR` guarda además
capturas del panel en cada estado.
"""

import os
import pickle
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
FIXTURE = ROOT / "dist" / "qa_fixture.pkl"

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

SHOTS = None
if "--shots" in sys.argv:
    SHOTS = Path(sys.argv[sys.argv.index("--shots") + 1])
    SHOTS.mkdir(parents=True, exist_ok=True)

if not FIXTURE.exists():
    raise SystemExit(f"✗ Falta {FIXTURE}. Ejecuta primero scripts/qa_fixture.py")
FX = pickle.load(open(FIXTURE, "rb"))
MUN = FX.get("municipio_prueba", "030101")

from qgis.core import QgsApplication, QgsProject, NULL                # noqa: E402
from qgis.PyQt.QtWidgets import QMainWindow, QLabel, QProgressBar     # noqa: E402
from qgis.PyQt.QtCore import QThread, pyqtSignal, Qt                  # noqa: E402

QgsApplication.setPrefixPath("/Applications/QGIS.app/Contents/MacOS", True)
APP = QgsApplication([], bool(SHOTS))
APP.initQgis()

# ── Estado de las comprobaciones ─────────────────────────────────────────────

FALLOS = []
OK = 0


def check(condicion, descripcion, detalle=""):
    global OK
    if condicion:
        OK += 1
        print(f"  ✓ {descripcion}")
    else:
        FALLOS.append(f"{descripcion}{' — ' + detalle if detalle else ''}")
        print(f"  ✗ {descripcion}{' — ' + detalle if detalle else ''}")
    return bool(condicion)


def seccion(titulo):
    print(f"\n{'=' * 70}\n{titulo}\n{'=' * 70}")


# ── Motor sustituido por el fixture ──────────────────────────────────────────

import qcensosbo.core.query_engine as qe                              # noqa: E402
qe._register_hard_exit = lambda: None
qe._hard_exit_registered = True
qe.duckdb_available = lambda: True

import qcensosbo.core.aggregator as am                                # noqa: E402
import qcensosbo.panel.dock_panel as dp                               # noqa: E402

am.get_var_descriptions = lambda a, t=None: FX["descs"].get((a, t), {})
am.get_var_types = lambda a, t=None: FX["types"].get((a, t), {})
_labels_reales = am.get_value_labels


def _labels(anio, variable, tabla=None):
    if tabla in dp.fichas.TABLAS:            # resueltas en el propio módulo
        return _labels_reales(anio, variable, tabla)
    return FX["labels"].get((anio, variable, tabla), {})


am.get_value_labels = _labels
dp.duckdb_available = lambda: True
dp.get_value_labels = _labels
dp.get_var_descriptions = am.get_var_descriptions
dp.get_var_types = am.get_var_types
dp.variable_coverage = lambda *a, **k: (None, None)
dp.distinct_values = lambda urls, v, **k: []


class ColumnsWorkerFake(QThread):
    done = pyqtSignal(list, dict, dict, int)

    def __init__(self, path_or_url, anio, tabla=None, remote=False, token=0):
        super().__init__()
        self.args = (anio, tabla, token)

    def start(self):
        anio, tabla, token = self.args
        self.done.emit(FX["cols"].get((anio, tabla), []),
                       FX["descs"].get((anio, tabla), {}),
                       FX["types"].get((anio, tabla), {}), token)

    def isRunning(self):
        return False


class CategoriesWorkerFake(QThread):
    done = pyqtSignal(list, int)
    # Se puede forzar a devolver siempre [] para probar la red de seguridad.
    vaciar = False

    def __init__(self, urls, variable, token=0):
        super().__init__()
        self.args = (variable, token)

    def start(self):
        variable, token = self.args
        vals = ([] if CategoriesWorkerFake.vaciar
                else FX["distinct"].get((2024, "personas", variable), []))
        self.done.emit(vals, token)

    def isRunning(self):
        return False


class MapWorkerFake(QThread):
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    done = pyqtSignal(object)
    error = pyqtSignal(str)
    ultimo = None

    def __init__(self, anio, tabla, nivel, variable, agg, category,
                 departamento=None, sql_expr=None, municipio=None, area=None):
        super().__init__()
        self.key = (anio, tabla, nivel, variable, agg, category, departamento,
                    municipio, area, sql_expr)

    def start(self):
        MapWorkerFake.ultimo = self.key
        if self.key in FX["agg"]:
            self.done.emit({"df": FX["agg"][self.key],
                            "national": FX["nat"][self.key],
                            "cobertura": FX["cobertura"].get(self.key, (None, None))})
            return
        # Coincidencia laxa por (año, tabla, nivel, municipio) para variantes de estilo.
        for k, df in FX["agg"].items():
            if k[:3] == self.key[:3] and k[7] == self.key[7]:
                self.done.emit({"df": df, "national": FX["nat"][k],
                                "cobertura": FX["cobertura"].get(k, (None, None))})
                return
        self.error.emit(f"[fixture] sin datos para {self.key}")

    def isRunning(self):
        return False


class GeomWorkerFake(QThread):
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    done = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, municipio, area=None):
        super().__init__()
        self.municipio, self.area = municipio, area

    def start(self):
        areas = [self.area] if self.area else ["urbana", "rural"]
        self.done.emit({a: FX.get(f"geoms_{self.municipio}_{a}", []) for a in areas})

    def isRunning(self):
        return False


dp.ColumnsWorker = ColumnsWorkerFake
dp.CategoriesWorker = CategoriesWorkerFake
dp.MapWorker = MapWorkerFake
dp.GeomWorker = GeomWorkerFake

# ── iface falso ──────────────────────────────────────────────────────────────

MSGS = []


class BarraFalsa:
    def pushWarning(self, t, m): MSGS.append(("WARN", m))
    def pushCritical(self, t, m): MSGS.append(("CRIT", m))
    def pushSuccess(self, t, m): MSGS.append(("OK", m))


class LienzoFalso:
    def setExtent(self, e): pass
    def refresh(self): pass


class IfaceFalso:
    def __init__(self):
        self._w, self._b, self._c = QMainWindow(), BarraFalsa(), LienzoFalso()

    def mainWindow(self): return self._w
    def messageBar(self): return self._b
    def mapCanvas(self): return self._c


iface = IfaceFalso()
panel = dp.CensosBOPanel(iface)
if SHOTS:
    panel.setFloating(True)
    panel.show()


def bombear(n=25):
    """Procesa la cola de eventos, incluidos los deleteLater pendientes.

    Sin vaciar los DeferredDelete, los widgets que `_clear_stat_bars` da de baja
    siguen pintados y las capturas salen con el resumen anterior encima."""
    from qgis.PyQt.QtCore import QEvent
    for _ in range(n):
        APP.processEvents()
    APP.sendPostedEvents(None, QEvent.DeferredDelete)
    APP.processEvents()


def captura(nombre, ancho=300):
    if not SHOTS:
        return
    bombear()
    alto = panel.widget().widget().sizeHint().height() + 40
    panel.resize(ancho, alto)
    bombear()
    panel.grab().save(str(SHOTS / f"{nombre}.png"))


def sel(combo, data):
    i = combo.findData(data)
    assert i >= 0, f"no existe {data!r} en {[combo.itemData(j) for j in range(combo.count())]}"
    combo.setCurrentIndex(i)


def opciones(combo):
    return [combo.itemData(i) for i in range(combo.count())]


def visible(w):
    return w.isVisibleTo(panel)


def resumen_textos():
    out = []
    for i in range(panel.stats_bars_layout.count()):
        w = panel.stats_bars_layout.itemAt(i).widget()
        if w is None:
            continue
        if isinstance(w, QLabel):
            out.append(w.text())
        else:
            out.extend(l.text() for l in w.findChildren(QLabel))
            out.extend(b.format() for b in w.findChildren(QProgressBar))
    return out


def rellenos():
    vals = []
    for i in range(panel.stats_bars_layout.count()):
        w = panel.stats_bars_layout.itemAt(i).widget()
        if w is not None and not isinstance(w, QLabel):
            vals.extend(b.value() for b in w.findChildren(QProgressBar))
    return vals


def consultar():
    MSGS.clear()
    panel._on_consultar_clicked()
    bombear(5)


try:
    # ─────────────────────────────────────────────────────────────────────────
    seccion("A · Ancho del panel: nada se desborda")
    bombear()
    contenedor = panel.widget().widget()
    ancho_pedido = contenedor.sizeHint().width()
    check(ancho_pedido <= panel.minimumWidth(),
          f"el contenido cabe en el ancho mínimo ({panel.minimumWidth()} px)",
          f"pide {ancho_pedido} px")
    for nombre, c in (("año", panel.combo_anio), ("tabla", panel.combo_tabla),
                      ("nivel", panel.combo_nivel), ("variable", panel.combo_variable),
                      ("agregación", panel.combo_agg),
                      ("categoría", panel.combo_categoria)):
        check(c.sizeHint().width() <= panel.minimumWidth() - 60,
              f"el combo de {nombre} no estira el panel",
              f"sizeHint {c.sizeHint().width()} px")
    captura("01_inicial")

    # ─────────────────────────────────────────────────────────────────────────
    seccion("B · Estado inicial y opción de conteo")
    check(panel.combo_variable.currentData() == dp.CONTEO_KEY,
          "arranca en «Conteo de registros»",
          str(panel.combo_variable.currentData()))
    check(panel._current_var_type == "count", "el tipo interno es 'count'")
    check(opciones(panel.combo_agg) == [dp.CONTEO_KEY],
          "la única agregación del conteo es el propio conteo",
          str(opciones(panel.combo_agg)))
    check(visible(panel.combo_clasificacion),
          "la clasificación está visible (el conteo es un mapa graduado)")
    check(not visible(panel.combo_categoria), "la categoría está oculta")
    check(not panel.btn_generar.isEnabled(),
          "«Generar mapa» empieza deshabilitado")
    consultar()
    check(not MSGS, "consultar el conteo por defecto no da avisos", str(MSGS))
    check(panel.btn_generar.isEnabled(), "tras consultar se habilita «Generar mapa»")
    textos = resumen_textos()
    check(any("Conteo de registros" in t for t in textos),
          "el resumen titula «Conteo de registros»", str(textos[:3]))
    check(any(t == "11.365.333" for t in textos),
          "la referencia nacional usa formato español (11.365.333)",
          str([t for t in textos if "365" in t]))
    captura("02_conteo")

    # ─────────────────────────────────────────────────────────────────────────
    seccion("C · El botón Generar no se habilita por la instalación del motor")
    panel._invalidate_result()
    panel._on_install_done(False, "fallo simulado")
    check(not panel.btn_generar.isEnabled(),
          "sigue deshabilitado tras un fallo de instalación")
    panel._on_install_done(True, "ok")
    check(not panel.btn_generar.isEnabled(),
          "sigue deshabilitado tras una instalación correcta sin consultar")

    # ─────────────────────────────────────────────────────────────────────────
    seccion("D · Tipos de variable → agregaciones coherentes")
    sel(panel.combo_anio, None) if False else panel.combo_anio.setCurrentIndex(0)
    sel(panel.combo_tabla, "personas")
    esperado = {
        "p26_edad": ("numeric", ["mean", "median", "sum", "std"]),
        "p25_sexo": ("categorical", ["pct_category", "mode"]),
    }
    for var, (tipo, aggs) in esperado.items():
        sel(panel.combo_variable, var)
        check(panel._current_var_type == tipo,
              f"{var} se reconoce como {tipo}", str(panel._current_var_type))
        check(opciones(panel.combo_agg) == aggs,
              f"{var} ofrece {aggs}", str(opciones(panel.combo_agg)))
    # `area` es geográfica: no debe aparecer en el selector (antes heredaba las
    # agregaciones de la variable anterior y ejecutaba la media de urbano/rural).
    check(panel.combo_variable.findData("area") < 0,
          "«area» no aparece en el selector de variables")
    for tecnica in ("idep", "iprov", "imun", "i00"):
        check(panel.combo_variable.findData(tecnica) < 0,
              f"«{tecnica}» no aparece en el selector")

    # ─────────────────────────────────────────────────────────────────────────
    seccion("E · Clasificación visible siempre que el mapa sea graduado")
    sel(panel.combo_variable, "p25_sexo")
    sel(panel.combo_agg, "pct_category")
    check(visible(panel.combo_clasificacion),
          "visible con «Porcentaje» (mapa graduado)")
    check(visible(panel.combo_categoria), "el selector de categoría se muestra")
    check(panel.combo_categoria.count() > 0,
          f"con {panel.combo_categoria.count()} categorías")
    sel(panel.combo_agg, "mode")
    check(not visible(panel.combo_clasificacion),
          "oculta con «Moda» (mapa categórico)")
    check(not visible(panel.combo_categoria), "la categoría se oculta con «Moda»")
    panel.chk_avanzado.setChecked(True)
    check(visible(panel.combo_clasificacion),
          "visible en modo SQL avanzado (mapa graduado)")
    check(not visible(panel.combo_variable) and not visible(panel.lbl_variable),
          "variable y su etiqueta se ocultan en modo SQL")
    check(not visible(panel.lbl_var_desc),
          "la descripción de la variable también se oculta en modo SQL")
    panel.chk_avanzado.setChecked(False)

    # ─────────────────────────────────────────────────────────────────────────
    seccion("F · Variables categóricas que el diccionario de etiquetas no cubre")
    dp.distinct_values = lambda urls, v, **k: FX["distinct"].get((2024, "personas", v), [])
    # Recorre TODAS las que no tienen etiquetas: ninguna debe quedar en el estado
    # que antes bloqueaba el panel («Porcentaje» exigiendo una categoría que la UI
    # no podía ofrecer, con el selector vacío y oculto).
    huerfanas = [v for v, t in FX["types"][(2024, "personas")].items()
                 if t in ("categorica", "texto")
                 and not _labels(2024, v, "personas")
                 and panel.combo_variable.findData(v) >= 0]
    check(len(huerfanas) >= 5,
          f"hay {len(huerfanas)} variables sin etiquetas para revisar")
    bloqueadas = []
    for v in huerfanas:
        sel(panel.combo_variable, v)
        bombear(10)
        i_pct = panel.combo_agg.findData("pct_category")
        pct_activo = i_pct >= 0 and panel.combo_agg.model().item(i_pct).isEnabled()
        sel(panel.combo_agg, "pct_category") if pct_activo else None
        bombear(5)
        if pct_activo and panel.combo_categoria.count() == 0:
            bloqueadas.append(v)
        # Consultar nunca debe pedir algo que no se pueda dar.
        consultar()
        if any("no tiene categorías" in m or "Elige una categoría" in m
               for _, m in MSGS):
            bloqueadas.append(v + " (aviso sin salida)")
    check(not bloqueadas,
          "ninguna queda sin salida: se rescatan con los valores del parquet",
          str(bloqueadas))
    sel(panel.combo_variable, "p35h1_provcod")
    bombear(10)
    check(panel.combo_categoria.count() == 21,
          "p35h1_provcod se llena con sus 21 valores distintos",
          str(panel.combo_categoria.count()))

    # Red de seguridad: si una variable no tuviera NINGUNA categoría posible
    # (hoy no ocurre con los datos publicados), «Porcentaje» se deshabilita en
    # vez de dejar al usuario chocando con un aviso.
    CategoriesWorkerFake.vaciar = True
    panel._cats_fallback.clear()
    sel(panel.combo_variable, "p26_edad")          # forzar un cambio real
    sel(panel.combo_variable, "p35h1_provcod")
    bombear(10)
    check(panel._estado_categorias() == "vacio",
          "[sintético] el estado de categorías es 'vacio'",
          panel._estado_categorias())
    i_pct = panel.combo_agg.findData("pct_category")
    check(i_pct >= 0 and not panel.combo_agg.model().item(i_pct).isEnabled(),
          "[sintético] sin ninguna categoría, «Porcentaje» se deshabilita")
    check(panel.combo_agg.currentData() != "pct_category",
          "[sintético] la selección cae en una agregación posible",
          str(panel.combo_agg.currentData()))
    check(visible(panel.lbl_agg_aviso) and "no está disponible" in panel.lbl_agg_aviso.text(),
          "[sintético] y se explica por qué", panel.lbl_agg_aviso.text())
    # Mientras la lectura está en vuelo, el panel NO debe afirmar que no hay
    # categorías: debe decir que las está leyendo.
    panel._cats_fallback.clear()
    panel._current_var_type = "categorical"
    estado = panel._estado_categorias()
    check(estado == "cargando",
          "en vuelo, el estado es 'cargando' (no 'vacio')", estado)
    panel._sync_controls()
    check("Leyendo" in panel.lbl_agg_aviso.text(),
          "y el aviso lo dice así", panel.lbl_agg_aviso.text())
    CategoriesWorkerFake.vaciar = False
    panel._cats_fallback.clear()

    # ─────────────────────────────────────────────────────────────────────────
    seccion("G · Porcentaje: base de cálculo declarada")
    sel(panel.combo_variable, "p39_tipoest")
    sel(panel.combo_agg, "pct_category")
    bombear(5)
    sel(panel.combo_categoria, "9")
    consultar()
    check(not MSGS, "consulta sin avisos", str(MSGS))
    textos = resumen_textos()
    check(any("casos con dato" in t for t in textos),
          "el resumen declara sobre cuántos casos se calculó",
          str([t for t in textos if "casos" in t]))
    check(any("no le aplica" in t for t in textos),
          "y advierte cuando la cobertura es baja")
    check(any("%" in t and "," in t for t in textos),
          "los porcentajes usan coma decimal",
          str([t for t in textos if "%" in t][:3]))
    captura("03_porcentaje")

    # ─────────────────────────────────────────────────────────────────────────
    seccion("H · Modo SQL avanzado no hereda el formato del combo oculto")
    sel(panel.combo_variable, "p25_sexo")
    sel(panel.combo_agg, "pct_category")
    panel.chk_avanzado.setChecked(True)
    panel.txt_sql.setPlainText("AVG(p26_edad)")
    # El fixture responde con la media de edad por departamento.
    consultar()
    textos = resumen_textos()
    check(not any(t.endswith("%") for t in textos),
          "ningún valor lleva el signo de porcentaje",
          str([t for t in textos if t.endswith("%")]))
    check(any("Expresión SQL" in t for t in textos), "el título dice «Expresión SQL»")
    check(any("AVG(p26_edad)" in t for t in textos),
          "y el resumen repite la expresión calculada", str(textos[:4]))
    captura("04_sql")
    panel.chk_avanzado.setChecked(False)

    # ─────────────────────────────────────────────────────────────────────────
    seccion("I · Ranking con barras proporcionales desde cero")
    sel(panel.combo_variable, "p26_edad")
    sel(panel.combo_agg, "mean")
    consultar()
    fills = [f for f in rellenos() if f > 0]
    check(fills and min(fills) > 70,
          "las barras de un rango 26–32 quedan todas altas (escala desde 0)",
          f"mínimo {min(fills) if fills else '—'}")
    textos = resumen_textos()
    check(any("Cuantos años cumplidos tiene" in t or "años cumplidos" in t
              for t in textos),
          "el título usa la etiqueta legible, no solo «p26_edad»",
          str(textos[:2]))
    check(any("(p26_edad)" in t for t in textos),
          "y conserva el nombre técnico entre paréntesis")
    captura("05_ranking")

    # ─────────────────────────────────────────────────────────────────────────
    seccion("J · Municipal: se declara lo que no tiene geometría")
    sel(panel.combo_nivel, "municipio")
    sel(panel.combo_depto, None)
    sel(panel.combo_variable, "p26_edad")
    sel(panel.combo_agg, "mean")
    consultar()
    textos = resumen_textos()
    check(panel.lbl_total.text() == "343",
          "el resumen cuenta los 343 municipios con dato", panel.lbl_total.text())
    check(any("sin geometría" in t for t in textos),
          "y avisa de los que no se podrán pintar",
          str([t for t in textos if "geometr" in t]))
    QgsProject.instance().removeAllMapLayers()
    MSGS.clear()
    panel._on_generar_clicked()
    capas = list(QgsProject.instance().mapLayers().values())
    check(len(capas) == 1, "se crea una capa", str(len(capas)))
    if capas:
        check(capas[0].featureCount() == 339,
              "con los 339 municipios que sí tienen polígono",
              str(capas[0].featureCount()))
    check(MSGS and "339 unidades dibujadas" in MSGS[-1][1],
          "el mensaje informa del conteo real de la capa",
          str(MSGS[-1:] and MSGS[-1][1]))
    check(MSGS and "no tienen geometría" in MSGS[-1][1],
          "y de los resultados sin geometría")
    captura("06_municipal")

    # ─────────────────────────────────────────────────────────────────────────
    seccion("K · Leyenda del mapa sin clases repetidas ni vacías")
    sel(panel.combo_nivel, "departamento")
    sel(panel.combo_variable, "p26_edad")
    sel(panel.combo_agg, "mean")
    for metodo in ("jenks", "quantile", "equal", "stddev"):
        sel(panel.combo_clasificacion, metodo)
        consultar()
        QgsProject.instance().removeAllMapLayers()
        panel._on_generar_clicked()
        capas = list(QgsProject.instance().mapLayers().values())
        if not check(len(capas) == 1, f"[{metodo}] capa creada"):
            continue
        r = capas[0].renderer()
        rangos = [(rg.lowerValue(), rg.upperValue(), rg.label())
                  for rg in r.ranges()]
        etiquetas = [rg[2] for rg in rangos]
        degeneradas = [rg for rg in rangos if rg[1] <= rg[0]]
        check(not degeneradas, f"[{metodo}] sin clases degeneradas", str(degeneradas))
        check(len(etiquetas) == len(set(etiquetas)),
              f"[{metodo}] sin etiquetas repetidas en la leyenda", str(etiquetas))
        # Ninguna feature con dato debe quedarse sin símbolo.
        valores = [f["valor_censo"] for f in capas[0].getFeatures()
                   if f["valor_censo"] != NULL]
        fuera = [v for v in valores
                 if not any(rg[0] <= v <= rg[1] for rg in rangos)]
        check(not fuera, f"[{metodo}] todos los valores caen en una clase", str(fuera))
        # El histograma del resumen debe usar los MISMOS cortes.
        from qcensosbo.core.layer_builder import class_bounds
        esperados = class_bounds(valores, metodo, 5)
        check([(round(a, 6), round(b, 6)) for a, b in esperados]
              == [(round(a, 6), round(b, 6)) for a, b, _ in rangos],
              f"[{metodo}] los cortes del mapa son los que mostró el resumen")

    # ─────────────────────────────────────────────────────────────────────────
    seccion("L · Moda: mapa categórico con etiquetas legibles")
    sel(panel.combo_variable, "p25_sexo")
    sel(panel.combo_agg, "mode")
    consultar()
    textos = resumen_textos()
    check(not any(t.endswith("%") for t in textos),
          "la moda no se formatea como porcentaje")
    QgsProject.instance().removeAllMapLayers()
    panel._on_generar_clicked()
    capas = list(QgsProject.instance().mapLayers().values())
    if check(len(capas) == 1, "capa creada"):
        cats = capas[0].renderer().categories()
        check(all("—" in c.label() for c in cats),
              "la leyenda muestra código y etiqueta",
              str([c.label() for c in cats]))
    captura("07_moda")

    # ─────────────────────────────────────────────────────────────────────────
    seccion("M · Fichas: aviso de cobertura")
    # El panel ya solo permite el nivel de unidad con fichas, así que el aviso que
    # se ve es el general. La advertencia de que sumar por territorio subestima
    # sigue viva para quien agregue por su cuenta (Processing / motor), y se prueba
    # llamando a la función directamente.
    check("92 %" in panel._aviso_fichas({"agg": "porcentaje", "nivel": "unidad"}),
          "a nivel unidad, la nota general de cobertura")
    aviso_total = panel._aviso_fichas({"agg": "total", "nivel": "municipio"})
    check("subestima" in aviso_total and "desigual" in aviso_total,
          "agregando por territorio, advierte del sesgo y de que es desigual",
          aviso_total[:120])
    check("85 %" in aviso_total and "94 %" in aviso_total,
          "con el rango real de cobertura medido")

    # ─────────────────────────────────────────────────────────────────────────
    seccion("N · Nivel manzano/comunidad")
    # Elegir la tabla de fichas ya fija el nivel en 'unidad' (ver sección O).
    sel(panel.combo_tabla, "fichas")
    bombear(5)
    check(panel._nivel() == "unidad",
          "elegir fichas fija el nivel en manzano/comunidad", panel._nivel())
    check(visible(panel.combo_municipio) and visible(panel.combo_area),
          "aparecen los selectores de municipio y área")
    consultar()
    check(MSGS and "municipio" in MSGS[0][1],
          "sin municipio, se pide elegirlo", str(MSGS))
    sel(panel.combo_depto, "03")
    check(panel.combo_municipio.count() > 40,
          f"el selector trae los municipios del departamento ({panel.combo_municipio.count()})")
    sel(panel.combo_municipio, MUN)
    sel(panel.combo_variable, "serv_agua_caneria")
    sel(panel.combo_agg, "porcentaje")
    consultar()
    check(not MSGS, "consulta sin avisos", str(MSGS))
    textos = resumen_textos()
    check(any("Clases del mapa" in t for t in textos),
          "el resumen presenta las clases del mapa", str(textos[:6]))
    check(any("Municipal" in t for t in textos),
          "la referencia se acota al municipio")
    fills = rellenos()
    check(len(set(fills)) > 1,
          "el histograma ya no es plano (antes: cuantiles = barras iguales)",
          str(fills))
    QgsProject.instance().removeAllMapLayers()
    sel(panel.combo_area, None)
    consultar()
    MSGS.clear()
    panel._on_generar_clicked()
    bombear(10)
    capas = QgsProject.instance().mapLayers().values()
    nombres = sorted(c.name() for c in capas)
    check(len(nombres) == 3,
          "se generan manzanos, comunidades y el contexto municipal", str(nombres))
    check(any("contexto" in n for n in nombres), "incluida la capa de contexto")
    check(MSGS and "con dato" in MSGS[-1][1],
          "el mensaje separa unidades con y sin ficha",
          str(MSGS[-1:] and MSGS[-1][1]))
    captura("08_manzanos")

    # ─────────────────────────────────────────────────────────────────────────
    seccion("O · El selector de nivel solo ofrece lo aplicable")
    panel.combo_anio.setCurrentIndex(panel.combo_anio.findText("2024"))
    bombear(5)
    sel(panel.combo_tabla, "personas")
    bombear(5)
    check(opciones(panel.combo_nivel) == ["departamento", "municipio"],
          "microdatos: departamental y municipal", str(opciones(panel.combo_nivel)))
    check(panel.combo_nivel.findData("unidad") < 0,
          "manzano/comunidad NO aparece (antes salía tachado)")
    check(visible(panel.combo_nivel) and visible(panel.lbl_nivel),
          "el campo Nivel se muestra porque hay más de una opción")
    check(not visible(panel.lbl_nivel_fijo), "sin la línea de nivel fijo")
    check("no está manzano/comunidad" in (panel.combo_nivel.property("ayuda") or ""),
          "y la ayuda explica por qué falta")

    sel(panel.combo_tabla, "fichas")
    bombear(5)
    check(opciones(panel.combo_nivel) == ["unidad"],
          "fichas: solo manzano/comunidad", str(opciones(panel.combo_nivel)))
    check(not visible(panel.combo_nivel) and not visible(panel.lbl_nivel),
          "el campo Nivel desaparece: la tabla determina el nivel")
    check(visible(panel.lbl_nivel_fijo)
          and "manzano urbano y comunidad rural" in panel.lbl_nivel_fijo.text(),
          "y una línea dice cuál es", panel.lbl_nivel_fijo.text())
    check(panel._nivel() == "unidad", "el nivel efectivo es 'unidad'", panel._nivel())
    check(visible(panel.combo_municipio) and visible(panel.combo_area)
          and visible(panel.combo_depto),
          "y aparecen departamento, municipio y área")

    sel(panel.combo_tabla, "unidades")
    bombear(5)
    check(opciones(panel.combo_nivel) == ["unidad"],
          "la tabla de unidades censales se comporta igual")

    sel(panel.combo_tabla, "personas")
    panel.combo_anio.setCurrentIndex(panel.combo_anio.findText("1976"))
    bombear(5)
    check(opciones(panel.combo_nivel) == ["departamento"],
          "1976: solo departamental", str(opciones(panel.combo_nivel)))
    check(not visible(panel.combo_nivel),
          "el campo Nivel desaparece con una sola opción")
    check("único disponible" in panel.lbl_nivel_fijo.text(),
          "y se explica que es el único del censo", panel.lbl_nivel_fijo.text())
    check(opciones(panel.combo_tabla) == ["personas", "viviendas"],
          "1976 solo ofrece personas y viviendas", str(opciones(panel.combo_tabla)))
    panel.combo_anio.setCurrentIndex(panel.combo_anio.findText("2024"))
    bombear(5)
    check(opciones(panel.combo_nivel) == ["departamento", "municipio"],
          "al volver a 2024 reaparecen los dos niveles")
    check(visible(panel.combo_nivel), "y el campo vuelve a mostrarse")

    seccion("O2 · Los dos pasos: mismo peso, énfasis progresivo")
    check(panel.btn_consultar.objectName() == panel.btn_generar.objectName()
          == "btn_paso",
          "comparten el estilo, así que tienen el mismo peso visual")
    panel._invalidate_result()
    check(panel.btn_consultar.property("paso_activo") is True
          and panel.btn_generar.property("paso_activo") is False,
          "sin resultado, el énfasis está en «1 · Consultar»",
          f"1={panel.btn_consultar.property('paso_activo')} "
          f"2={panel.btn_generar.property('paso_activo')}")
    check(not panel.btn_generar.isEnabled(), "y el paso 2 está deshabilitado")
    sel(panel.combo_variable, dp.CONTEO_KEY)
    consultar()
    check(panel.btn_generar.property("paso_activo") is True
          and panel.btn_consultar.property("paso_activo") is False,
          "con resultado, el énfasis pasa a «2 · Generar mapa»",
          f"1={panel.btn_consultar.property('paso_activo')} "
          f"2={panel.btn_generar.property('paso_activo')}")
    check(panel.btn_generar.isEnabled(), "y el paso 2 se habilita")
    sel(panel.combo_agg, panel.combo_agg.itemData(0))
    panel.combo_clasificacion.setCurrentIndex(1)   # no invalida (solo estilo)
    check(panel.btn_generar.property("paso_activo") is True,
          "cambiar la clasificación no devuelve el énfasis al paso 1")
    sel(panel.combo_nivel, "municipio")            # sí invalida
    check(panel.btn_consultar.property("paso_activo") is True,
          "cambiar un parámetro devuelve el énfasis al paso 1")

    seccion("O3 · Cada campo tiene ayuda con ejemplos")
    campos = {
        "Año": panel.combo_anio, "Tabla": panel.combo_tabla,
        "Nivel": panel.combo_nivel, "Departamento": panel.combo_depto,
        "Municipio": panel.combo_municipio, "Área": panel.combo_area,
        "Variable": panel.combo_variable, "Agregación": panel.combo_agg,
        "Clasificación": panel.combo_clasificacion, "Categoría": panel.combo_categoria,
    }
    for nombre, w in campos.items():
        ayuda = w.property("ayuda") or ""
        check(len(ayuda) > 40, f"{nombre} tiene ayuda", f"{len(ayuda)} caracteres")
    con_ejemplo = [n for n, w in campos.items()
                   if "Ejemplo" in (w.property("ayuda") or "")
                   or "•" in (w.property("ayuda") or "")]
    check(len(con_ejemplo) == len(campos),
          "todas incluyen un ejemplo o el desglose de sus opciones",
          str(sorted(set(campos) - set(con_ejemplo))))
    etiquetas = {"Año": None, "Tabla": None}
    check(panel.lbl_variable.toolTip() == dp.AYUDA["variable"],
          "el rótulo del campo también lleva la ayuda")

    # ─────────────────────────────────────────────────────────────────────────
    seccion("P · Formato numérico único (español)")
    casos = [(11365333, "11.365.333"), (30.49, "30,49"), (1234567.5, "1.234.567,5"),
             (0, "0"), (50.0, "50")]
    for valor, esperado in casos:
        check(dp.fmt_num(valor) == esperado,
              f"fmt_num({valor}) = {esperado}", dp.fmt_num(valor))
    check(dp.fmt_num(49.36, pct=True) == "49,4%",
          "porcentaje con coma decimal", dp.fmt_num(49.36, pct=True))

except Exception:
    traceback.print_exc()
    FALLOS.append("excepción no controlada (ver traza)")

# ── Resultado ────────────────────────────────────────────────────────────────

print(f"\n{'=' * 70}")
print(f"{OK} comprobaciones correctas, {len(FALLOS)} fallos")
for f in FALLOS:
    print(f"  ✗ {f}")
if SHOTS:
    print(f"\nCapturas en {SHOTS}")
sys.stdout.flush()
# os._exit: las capas Python vivas se destruirían después de exitQgis(), con GDAL
# ya cerrado → SIGSEGV en Py_FinalizeEx.
os._exit(1 if FALLOS else 0)
