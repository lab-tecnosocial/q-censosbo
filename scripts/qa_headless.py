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
import qcensosbo.core.universos as un                                  # noqa: E402
import qcensosbo.panel.dock_panel as dp                               # noqa: E402

am.get_var_descriptions = lambda a, t=None: FX["descs"].get((a, t), {})
am.get_var_types = lambda a, t=None: FX["types"].get((a, t), {})
# Universos y temas llegaron con censosbo 1.5.0. Se leen del fixture si está
# regenerado y, si no, quedan vacíos: el panel tiene que funcionar igual con un
# diccionario antiguo, y así también se prueba ese camino.
am.get_var_universos = lambda a, t=None: FX.get("universos", {}).get((a, t), {})
am.get_var_temas = lambda a, t=None: FX.get("temas", {}).get((a, t), {})
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
dp.get_var_universos = am.get_var_universos
dp.get_var_temas = am.get_var_temas
dp.variable_coverage = lambda *a, **k: (None, None)
dp.distinct_values = lambda urls, v, **k: []


class ColumnsWorkerFake(QThread):
    # Un solo dict, igual que el worker real: el diccionario de censosbo crece y
    # antes cada dato nuevo cambiaba la firma de la señal.
    done = pyqtSignal(dict, int)

    def __init__(self, path_or_url, anio, tabla=None, remote=False, token=0):
        super().__init__()
        self.args = (anio, tabla, token)

    def start(self):
        anio, tabla, token = self.args
        self.done.emit({
            "cols":      FX["cols"].get((anio, tabla), []),
            "descs":     FX["descs"].get((anio, tabla), {}),
            "types":     FX["types"].get((anio, tabla), {}),
            "universos": FX.get("universos", {}).get((anio, tabla), {}),
            "temas":     FX.get("temas", {}).get((anio, tabla), {}),
        }, token)

    def isRunning(self):
        return False


class CategoriesWorkerFake(QThread):
    done = pyqtSignal(list, int)
    # Se puede forzar a devolver siempre [] para probar la red de seguridad.
    vaciar = False
    # Último universo de tabla recibido, para comprobarlo desde los tests.
    ultimo_universo = "(sin llamar)"

    def __init__(self, urls, variable, token=0, universo_tabla=None):
        super().__init__()
        self.args = (variable, token)
        # El panel pasa el universo de la tabla (ver core/universos.py). El doble
        # lo guarda para poder afirmar que llega, aunque el fixture no filtre.
        self.universo_tabla = universo_tabla
        CategoriesWorkerFake.ultimo_universo = universo_tabla

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
    ultimo_casos_expr = "(sin llamar)"

    def __init__(self, anio, tabla, nivel, variable, agg, category,
                 departamento=None, sql_expr=None, municipio=None, area=None,
                 casos_expr=None):
        super().__init__()
        self.key = (anio, tabla, nivel, variable, agg, category, departamento,
                    municipio, area, sql_expr)
        # No entra en la clave: el tamaño de muestra no cambia qué datos pide el
        # panel, solo si puede avisar de las celdas frágiles. Se guarda para poder
        # comprobar desde los tests que los indicadores de ficha lo declaran.
        MapWorkerFake.ultimo_casos_expr = casos_expr

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
    # Qt6 exige cualificar el enum; el nombre cualificado existe también en Qt5.
    APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
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
        # Los dos porcentajes van juntos y primero: solo se diferencian en el
        # denominador (ver PCT_OPCIONES en el panel).
        "p25_sexo": ("categorical", ["pct_category", "pct_total", "mode"]),
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
    seccion("J · Municipal: cartografía completa, y red de seguridad si no lo fuera")
    sel(panel.combo_nivel, "municipio")
    sel(panel.combo_depto, None)
    sel(panel.combo_variable, "p26_edad")
    sel(panel.combo_agg, "mean")
    consultar()
    textos = resumen_textos()
    check(panel.lbl_total.text() == "343",
          "el resumen cuenta los 343 municipios con dato", panel.lbl_total.text())
    # Desde censosbo 1.6.0 la cartografía trae los 343, así que en 2024 no debe
    # aparecer ningún aviso de cobertura: si aparece, algo se rompió al regenerar
    # los GeoJSON (ver scripts/build_geo.R).
    check(not any("sin geometría" in t for t in textos),
          "y NO avisa de cobertura: los 343 tienen polígono",
          str([t for t in textos if "geometr" in t]))
    QgsProject.instance().removeAllMapLayers()
    MSGS.clear()
    panel._on_generar_clicked()
    capas = list(QgsProject.instance().mapLayers().values())
    check(len(capas) == 1, "se crea una capa", str(len(capas)))
    if capas:
        check(capas[0].featureCount() == 343,
              "con los 343 municipios", str(capas[0].featureCount()))
    check(MSGS and "343 unidades dibujadas" in MSGS[-1][1],
          "el mensaje informa del conteo real de la capa",
          str(MSGS[-1:] and MSGS[-1][1]))
    captura("06_municipal")

    # La red de seguridad se prueba por INYECCIÓN, no esperando que a los datos les
    # falten municipios: los censos anteriores a 2012 usan otra división y sus
    # códigos pueden no existir en la actual, y eso hay que seguir declarándolo.
    # El panel lo importa dentro de la función, así que hay que parchear el módulo
    # de origen, no el atributo de dock_panel.
    import qcensosbo.core.layer_builder as lb
    _cobertura_real = lb.cobertura_geo
    lb.cobertura_geo = lambda codigos, nivel, departamento=None: (
        max(len(set(map(str, codigos))) - 3, 0), ["999901", "999902", "999903"])
    try:
        consultar()
        textos = resumen_textos()
        check(any("sin geometría" in t for t in textos),
              "si faltara cobertura, el resumen lo declara",
              str([t for t in textos if "geometr" in t])[:90])
        check(any("999901" in t for t in textos),
              "y nombra los códigos que se quedarían sin pintar")
    finally:
        lb.cobertura_geo = _cobertura_real
    consultar()          # deja el estado limpio para las secciones siguientes

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

    # ─────────────────────────────────────────────────────────────────────────
    seccion("Q · El universo de la pregunta (censosbo 1.5.0)")
    # El diccionario del INE dice a quién se le hizo cada pregunta. Sin esto, un
    # promedio "de la población" que en realidad es de mayores de 19 años se lee
    # mal, que es el error más común del análisis censal.
    u = am.universo_legible
    for slug, esperado in [
        ("personas_7_mas",  "personas de 7 años o más"),
        ("personas_19_mas", "personas de 19 años o más"),
        ("mujeres_15_49",   "mujeres de 15 a 49 años"),
        ("todas_personas",  "todas las personas"),
    ]:
        check(u(slug) == esperado, f"{slug} → {esperado}", u(slug))
    # Un universo que el INE añada mañana tiene que salir legible sin tocar código.
    check(u("hombres_18_mas") == "hombres de 18 años o más",
          "un universo nuevo se deriva por patrón", u("hombres_18_mas"))
    check(u(None) is None and u("") is None,
          "sin universo devuelve None (diccionario anterior a 1.5.0)")

    universos = FX.get("universos", {}).get((2024, "personas"), {})
    if universos:
        panel.combo_anio.setCurrentText("2024")
        sel(panel.combo_tabla, "personas")
        bombear()
        # Una variable con universo restringido: debe anunciarse.
        restringida = next(
            (v for v, txt in universos.items() if "años o más" in txt), None)
        check(restringida is not None,
              "hay variables con universo restringido en 2024",
              str(restringida))
        if restringida:
            sel(panel.combo_variable, restringida)
            bombear()
            desc = panel.lbl_var_desc.text()
            check("Se preguntó a:" in desc,
                  f"el panel declara el universo de {restringida}",
                  desc.replace("\n", " · ")[:90])
            consultar()
            check(any("Pregunta aplicada a:" in t for t in resumen_textos()),
                  "y el resumen del resultado también lo dice")
        # Las obvias no ensucian: "todas las personas" no se anuncia.
        obvia = next(
            (v for v, txt in universos.items() if txt == "todas las personas"), None)
        if obvia:
            sel(panel.combo_variable, obvia)
            bombear()
            check("Se preguntó a:" not in panel.lbl_var_desc.text(),
                  "un universo obvio no se anuncia (sería ruido)",
                  panel.lbl_var_desc.text()[:70])
    else:
        print("    (el fixture no trae universos: regenéralo para probar esto)")

    # ── Los saltos del cuestionario, la otra mitad del universo ──────────────
    # El universo del diccionario es el filtro grueso; el resto de a quién llegó
    # la pregunta está en el texto de las preguntas anteriores («1 Sí (PASE A
    # P49)»). Sin esto, el panel dice "personas de 7 años o más" y deja creer que
    # a las demás se les preguntó. Las cifras que lo justifican —los 3.531 «Sin
    # dato» de la p45 predichos caso por caso— están en scripts/qa_redatam.py;
    # aquí se comprueba lo que solo se ve desde el panel.
    panel.combo_anio.setCurrentText("2024")
    sel(panel.combo_tabla, "personas")
    bombear()
    if panel.combo_variable.findData("p45_agro") >= 0:
        sel(panel.combo_variable, "p45_agro")
        bombear()
        desc = panel.lbl_var_desc.text()
        check("no a todas" in desc,
              "el panel avisa de que el cuestionario salta la p45",
              desc.replace("\n", " · ")[:110])
        ayuda = panel.combo_variable.property("ayuda") or ""
        check("pregunta 43" in ayuda and "pregunta 44" in ayuda,
              "y la ayuda de la variable dice qué preguntas la condicionan",
              ayuda[-120:].replace("\n", " · "))
        consultar()
        nota = " ".join(resumen_textos())
        check("dentro de ese universo" in nota and "pregunta 43" in nota,
              "el resumen junta el universo declarado y los saltos")
        # Una variable sin saltos no debe inventarse ninguno.
        if panel.combo_variable.findData("p26_edad") >= 0:
            sel(panel.combo_variable, "p26_edad")
            bombear()
            check("no a todas" not in panel.lbl_var_desc.text(),
                  "una variable sin saltos no anuncia ninguno",
                  panel.lbl_var_desc.text()[:70])
    else:
        print("    (el fixture no trae p45_agro: regenéralo para probar esto)")

    # Las fichas son agregados del INE por unidad, no preguntas: sin universo.
    sel(panel.combo_tabla, "fichas")
    bombear()
    check(panel._var_universos == {},
          "las fichas no declaran universo (no son preguntas)")

    # ─────────────────────────────────────────────────────────────────────────
    seccion("Q2 · El universo de la TABLA de viviendas (censosbo 1.7.0)")
    # Otra cosa que lo anterior: eso es a quién se le hizo la pregunta, esto es
    # qué filas son un caso. La tabla de viviendas del censo trae registros de
    # personas censadas fuera de una vivienda (calle, tránsito) que el INE no
    # cuenta como viviendas. Aquí se comprueba que la condición llega desde el
    # panel hasta el motor; las cifras las fija scripts/qa_universos.py.
    # La condición SQL en sí (y las cifras que produce) las fija
    # scripts/qa_universos.py contra los datos reales; aquí se comprueba lo que
    # solo se ve desde el panel: que el resultado lo declare al usuario.
    check(un.universo_sql(2024, "viviendas") is not None
          and "v01_tipoviv" in un.universo_sql(2024, "viviendas"),
          "la tabla de viviendas tiene condición de universo",
          str(un.universo_sql(2024, "viviendas")))
    check(un.universo_sql(2024, "personas") is None,
          "las demás tablas no la tienen")

    panel.combo_anio.setCurrentText("2024")
    sel(panel.combo_tabla, "viviendas")
    sel(panel.combo_nivel, "departamento")
    bombear()
    consultar()
    bombear()
    check(any("calle o en tránsito" in t for t in resumen_textos()),
          "el resumen de viviendas declara qué filas quedaron fuera",
          " · ".join(resumen_textos())[:140])

    # En las demás tablas no hay nada que descontar: anunciarlo sería ruido.
    sel(panel.combo_tabla, "personas")
    bombear()
    consultar()
    bombear()
    check(not any("calle o en tránsito" in t for t in resumen_textos()),
          "en personas el resumen no lo menciona",
          " · ".join(resumen_textos())[:140])

    # ─────────────────────────────────────────────────────────────────────────
    seccion("Q3 · Los dos denominadores del porcentaje (v0.6.0)")
    # El mismo cruce da dos números correctos según qué haya en el denominador, y
    # el error está en no decir cuál: en la p45 de Santiváñez son 35,11 % sobre los
    # casos con dato y 19,50 % sobre todos los registros. Las cifras las fija
    # scripts/qa_redatam.py; aquí, que el panel ofrezca las dos y las declare.
    claves_pct = ("pct_category", "pct_total")
    panel.combo_anio.setCurrentText("2024")
    sel(panel.combo_tabla, "personas")
    sel(panel.combo_nivel, "departamento")
    sel(panel.combo_variable, "p25_sexo")
    bombear()
    ofrecidas = [panel.combo_agg.itemData(i) for i in range(panel.combo_agg.count())]
    check(all(k in ofrecidas for k in claves_pct),
          "el panel ofrece los dos denominadores", str(ofrecidas))
    etiquetas = [panel.combo_agg.itemText(i) for i in range(panel.combo_agg.count())
                 if panel.combo_agg.itemData(i) in claves_pct]
    check(all("casos con dato" in e or "todos los registros" in e
              for e in etiquetas),
          "y el denominador está en la etiqueta, no solo en la documentación",
          " | ".join(etiquetas))
    check(ofrecidas[0] == "pct_category",
          "el que reproduce las cifras del INE queda por defecto", str(ofrecidas[:1]))

    # Cada medida declara su base de cálculo. La cobertura la trae el resultado
    # (la calcula el worker), así que en vez de driblar la UI se llama al resumen
    # con el par (registros, casos con dato) que se quiere probar: es lo que
    # decide el texto, y así el caso con hueco no depende del fixture.
    ctx_pct = dict(anio=2024, tabla="personas", nivel="departamento", depto=None,
                   municipio=None, area=None, variable="p25_sexo",
                   category="2", sql_expr=None, sql_libre=False, key=None)
    for clave, esperado in (("pct_category", "casos con dato"),
                            ("pct_total", "tengan dato o no")):
        panel._show_result_summary(
            {"df": FX["agg"][(2024, "personas", "departamento", "p25_sexo",
                              "pct_category", "2", None, None, None, None)],
             "national": None, "cobertura": (1000, 400)},
            dict(ctx_pct, agg=clave))
        bombear()
        notas = " ".join(resumen_textos())
        check(esperado in notas, f"[{clave}] el resumen declara su denominador",
              notas[:170])
        if clave == "pct_total":
            check("NO es comparable" in notas,
                  "y avisa de que ese porcentaje no es una cifra del INE",
                  notas[:170])

    # ─────────────────────────────────────────────────────────────────────────
    seccion("Q4 · Celdas frágiles: se marcan, no se suprimen (v0.6.0)")
    # A nivel de manzano hay unidades de dos o tres personas: un «50 %» sobre dos
    # casos se copia igual que uno sobre dos mil. El plugin no oculta nada —el INE
    # publica los microdatos completos—, cuenta las unidades afectadas y pone el
    # tamaño de muestra de cada una en la capa.
    from qcensosbo.core.layer_builder import CASOS_FRAGIL, unidades_fragiles
    check(CASOS_FRAGIL == 5, "el umbral son 5 casos", str(CASOS_FRAGIL))

    clave_fragil = (2024, "personas", "municipio", "p52_pais_mov_cod",
                    "pct_category", "1", "09", None, None, None)
    df_fragil = FX["agg"].get(clave_fragil)
    if df_fragil is not None:
        check("casos" in df_fragil.columns,
              "el motor trae el tamaño de muestra de cada unidad",
              str(list(df_fragil.columns)))
        n_frag, n_val = unidades_fragiles(df_fragil)
        check(n_frag > 0 and n_val > n_frag,
              "en el peor caso del censo hay unidades frágiles y otras no",
              f"{n_frag} de {n_val}")
        check(n_val < len(df_fragil),
              "las unidades sin casos no cuentan como frágiles (son sin dato)",
              f"{n_val} con valor de {len(df_fragil)} filas")
        # Y que el aviso salga en el resumen, con el nombre del campo de la capa.
        # Se llama al resumen con este df a propósito: driblando la UI, la
        # categoría que quedara elegida decidiría qué escenario del fixture
        # responde, y lo que se prueba aquí es el aviso, no el enrutado.
        panel._show_result_summary(
            {"df": df_fragil, "national": None, "cobertura": (None, None)},
            dict(anio=2024, tabla="personas", nivel="municipio", depto="09",
                 municipio=None, area=None, variable="p52_pais_mov_cod",
                 agg="pct_category", category="1", sql_expr=None,
                 sql_libre=False, key=None))
        bombear()
        notas = " ".join(resumen_textos())
        check("menos de 5 casos" in notas and "casos_censo" in notas,
              "el resumen avisa y dice dónde está el número de casos",
              notas[:170])
    else:
        print("    (el fixture no trae el escenario de Pando: regenéralo)")

    # Un conteo no lleva aviso: su valor ES el número de casos, así que decir que
    # son pocos sería ruido —y llamarlo poco fiable, falso: un conteo es exacto—.
    sel(panel.combo_nivel, "departamento")
    sel(panel.combo_depto, None)
    sel(panel.combo_variable, dp.CONTEO_KEY)
    bombear()
    consultar()
    bombear()
    check(not any("menos de 5 casos" in t for t in resumen_textos()),
          "un conteo no lleva aviso de fragilidad (el valor ya es el conteo)",
          " · ".join(resumen_textos())[:120])

    # ─────────────────────────────────────────────────────────────────────────
    seccion("T · Documentación conceptual del INE en el tooltip")
    from qcensosbo.core import docs_vars
    panel.combo_anio.setCurrentText("2024")
    sel(panel.combo_tabla, "personas")
    bombear()
    sel(panel.combo_variable, "p26_edad")
    bombear()
    ayuda = panel.combo_variable.toolTip()
    check("edad" in ayuda.lower(), "el tooltip trae la definición del INE",
          ayuda.replace("\n", " ")[:80])
    check("Pregunta en campo" in ayuda,
          "y la pregunta tal como se leyó en campo")

    # Las derivadas explican cómo se construyeron: es lo que evita malinterpretarlas.
    doc = docs_vars.documentacion(2024, "nivel_edu", "personas")
    check(doc is not None, "nivel_edu está documentada")
    if doc:
        check(bool((doc.get("universo_literal") or "").strip()),
              "con su universo redactado en palabras",
              (doc.get("universo_literal") or "")[:60])

    # Sin documentación se conserva la ayuda general del campo, no un tooltip vacío.
    sel(panel.combo_variable, dp.CONTEO_KEY)
    bombear()
    check(panel.combo_variable.toolTip() == dp.AYUDA["variable"],
          "el conteo de registros mantiene la ayuda general del campo")
    sel(panel.combo_tabla, "fichas")
    bombear()
    primera = panel.combo_variable.itemData(0)
    sel(panel.combo_variable, primera)
    bombear()
    check(panel.combo_variable.toolTip() == dp.AYUDA["variable"],
          "y los indicadores de ficha también (no son preguntas del cuestionario)")
    check(docs_vars.texto_ayuda(2024, "no_existe_esta", "personas") == "",
          "una variable no documentada devuelve cadena vacía, no un error")
    sel(panel.combo_tabla, "personas")
    bombear()

    # ─────────────────────────────────────────────────────────────────────────
    seccion("S · Densidad por km²")
    from qcensosbo.core.layer_builder import geo_superficies
    sup_dep = geo_superficies("departamento")
    check(len(sup_dep) == 9, f"hay superficie de los 9 departamentos ({len(sup_dep)})")
    check(len(geo_superficies("municipio")) == 343,
          "y de los 343 municipios", str(len(geo_superficies("municipio"))))

    panel.combo_anio.setCurrentText("2024")
    sel(panel.combo_tabla, "personas")
    sel(panel.combo_nivel, "departamento")
    sel(panel.combo_variable, dp.CONTEO_KEY)
    bombear()
    check(visible(panel.chk_densidad), "la casilla aparece con Conteo")
    # El estado sale de la condición lógica, no de isVisible(): un panel acoplado y
    # colapsado —o QGIS headless— devuelve isVisible() False aunque el campo esté a
    # la vista, y la densidad se apagaba sin decir nada.
    check(panel._puede_densidad() and not panel.chk_densidad.isVisible(),
          "el estado no depende de isVisible() del widget")

    consultar()
    total_sin = float(panel._agg_result[1]["valor"].sum())
    panel.chk_densidad.setChecked(True)
    bombear()
    textos = resumen_textos()
    check(any("km²" in t for t in textos),
          "el título dice que el valor es por km²",
          str([t for t in textos if "km" in t])[:80])

    # El valor de un departamento concreto tiene que ser población / km².
    df = panel._a_densidad(panel._agg_result[1], panel._agg_result[2])
    fila = df[df["geo_code"] == "02"].iloc[0]          # La Paz
    crudo = panel._agg_result[1]
    poblacion = float(crudo[crudo["geo_code"] == "02"]["valor"].iloc[0])
    esperado = poblacion / sup_dep["02"]
    check(abs(float(fila["valor"]) - esperado) < 1e-6,
          f"La Paz = {esperado:.1f} hab/km²", f"{float(fila['valor']):.4f}")
    check(float(fila["valor"]) < poblacion,
          "y es mucho menor que el conteo bruto")

    # Es post-proceso: activar la densidad NO debe invalidar la consulta.
    check(panel.btn_generar.isEnabled(),
          "activar la densidad no obliga a volver a consultar")
    check(panel._agg_result is not None and
          abs(float(panel._agg_result[1]["valor"].sum()) - total_sin) < 1e-6,
          "y el resultado guardado sigue siendo el crudo, sin dividir")

    # No tiene sentido sobre promedios ni porcentajes: la casilla desaparece.
    sel(panel.combo_variable, "p26_edad")
    sel(panel.combo_agg, "mean")
    bombear()
    check(not visible(panel.chk_densidad), "con Media la casilla no aparece")
    check(not panel.chk_densidad.isChecked(),
          "y se desmarca sola, para no arrastrar un estado imposible")

    # Tampoco en manzano/comunidad: no hay superficie declarada de las unidades.
    sel(panel.combo_tabla, "fichas")
    bombear()
    check(not visible(panel.chk_densidad),
          "en manzano/comunidad no se ofrece (sin superficie por unidad)")
    sel(panel.combo_tabla, "personas")
    sel(panel.combo_nivel, "departamento")
    sel(panel.combo_variable, dp.CONTEO_KEY)
    bombear()

    # ─────────────────────────────────────────────────────────────────────────
    seccion("R · Filtro por tema del catálogo del INE")
    panel.combo_anio.setCurrentText("2024")
    sel(panel.combo_tabla, "personas")
    bombear()
    temas = opciones(panel.combo_tema)
    check(visible(panel.combo_tema), "el campo Tema se ve cuando hay temas")
    check(panel.combo_tema.itemData(0) == dp.TODOS_TEMAS,
          "la primera opción es «Todos los temas» (sin filtro)")
    check(len(temas) > 5, f"ofrece los temas del censo ({len(temas) - 1})",
          "; ".join(temas[1:4]))
    # `opciones()` devuelve los datos; el conteo va en el texto visible.
    textos_tema = [panel.combo_tema.itemText(i)
                   for i in range(1, panel.combo_tema.count())]
    check(all(t.rstrip().endswith(")") for t in textos_tema),
          "cada tema dice cuántas variables tiene",
          textos_tema[0] if textos_tema else "")

    todas = len(opciones(panel.combo_variable))
    # Un tema concreto tiene que reducir la lista, y nunca quedarse vacío: solo se
    # ofrecen temas con al menos una variable a la vista.
    slug_edu = next((panel.combo_tema.itemData(i)
                     for i in range(panel.combo_tema.count())
                     if "ducaci" in panel.combo_tema.itemText(i)), None)
    check(slug_edu is not None, "hay un tema de educación", str(slug_edu))
    if slug_edu:
        sel(panel.combo_tema, slug_edu)
        bombear()
        filtradas = opciones(panel.combo_variable)
        check(1 < len(filtradas) < todas,
              f"filtrar por tema reduce la lista ({todas} → {len(filtradas)})")
        check(filtradas[0] == dp.CONTEO_KEY,
              "el conteo de registros sigue disponible en cualquier tema")
        # Todas las que quedan pertenecen de verdad al tema.
        datos = [panel.combo_variable.itemData(i)
                 for i in range(1, panel.combo_variable.count())]
        check(all((panel._var_temas.get(v) or ("",))[0] == slug_edu for v in datos),
              "y todas las variables listadas son de ese tema")

        # Volver a «Todos» restaura la lista completa.
        sel(panel.combo_tema, dp.TODOS_TEMAS)
        bombear()
        check(len(opciones(panel.combo_variable)) == todas,
              "«Todos los temas» restaura la lista completa")

    # Un año cuyo diccionario tenga menos temas no debe dejar el filtro incoherente.
    panel.combo_anio.setCurrentText("1976")
    bombear()
    sel(panel.combo_tabla, "personas")
    bombear()
    if panel._temas_disponibles:
        check(panel.combo_tema.itemData(0) == dp.TODOS_TEMAS,
              "al cambiar de censo el filtro se repuebla con los temas de ese año",
              f"{panel.combo_tema.count() - 1} temas en 1976")
    # En el modo SQL no hay lista de variables que acotar.
    panel.combo_anio.setCurrentText("2024")
    bombear()
    panel.chk_avanzado.setChecked(True)
    bombear()
    check(not visible(panel.combo_tema), "el modo SQL oculta el campo Tema")
    panel.chk_avanzado.setChecked(False)
    bombear()
    # En fichas el mismo selector agrupa por BLOQUE de la ficha, con otro rótulo.
    sel(panel.combo_tabla, "fichas")
    bombear()
    check(visible(panel.combo_tema), "en fichas el selector sigue disponible")
    check(panel.lbl_tema.text() == "Bloque:",
          "y se llama «Bloque:», no «Tema:»", panel.lbl_tema.text())
    check("ficha del INE" in (panel.combo_tema.toolTip() or ""),
          "con la ayuda propia de los bloques")
    bloques = opciones(panel.combo_tema)
    check(len(bloques) > 8, f"ofrece los bloques de la ficha ({len(bloques) - 1})")

    todas_fichas = len(opciones(panel.combo_variable))
    serv = next((panel.combo_tema.itemData(i)
                 for i in range(panel.combo_tema.count())
                 if "ervicios" in panel.combo_tema.itemText(i)), None)
    if check(serv is not None, "hay un bloque de servicios básicos", str(serv)):
        sel(panel.combo_tema, serv)
        bombear()
        filtradas = opciones(panel.combo_variable)
        check(1 <= len(filtradas) < todas_fichas,
              f"filtrar por bloque reduce la lista ({todas_fichas} → {len(filtradas)})")
        # Al filtrar, el prefijo del bloque desaparece: ya lo dice el selector.
        textos = [panel.combo_variable.itemText(i)
                  for i in range(panel.combo_variable.count())]
        check(all("·" not in t for t in textos),
              "y las etiquetas ya no repiten el nombre del bloque",
              textos[0] if textos else "")
        sel(panel.combo_tema, dp.TODOS_TEMAS)
        bombear()
        check(len(opciones(panel.combo_variable)) == todas_fichas,
              "volver a «Todos» restaura las 245 opciones")
        check(any("·" in panel.combo_variable.itemText(i)
                  for i in range(panel.combo_variable.count())),
              "y el prefijo del bloque vuelve a mostrarse")

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
