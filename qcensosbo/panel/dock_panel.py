"""
Panel lateral del plugin Q-CensosBo.

Controles:
  - Año / Tabla / Nivel / Departamento (+ Municipio y Área en el nivel de
    manzano/comunidad)
  - Variable (todas las columnas del parquet, leídas del schema; muestra el tipo
    abreviado). En las tablas de fichas viene del catálogo empaquetado.
  - Agregación según tipo: categórica → Porcentaje | Moda; numérica → Media |
    Mediana | Suma | Desviación. Fichas → Total | % del total del bloque.
  - Categoría (visible solo con "Porcentaje", con etiquetas del codebook)

Velocidad:
  - DuckDB es el único motor: consulta el parquet remoto sin descargarlo, o lee
    el parquet local cacheado. Mismo SQL en ambos casos.
"""

import os
from qgis.PyQt.QtWidgets import (
    QDockWidget, QWidget, QVBoxLayout, QHBoxLayout, QFormLayout,
    QComboBox, QLabel, QProgressBar, QPushButton, QGroupBox,
    QScrollArea, QFrame, QCheckBox, QPlainTextEdit,
)
from qgis.PyQt.QtCore import Qt, pyqtSignal, QThread

from ..core.data_loader import get_tables_for_year
from ..core import fichas
from ..core.query_engine import (
    duckdb_available, install_duckdb,
    get_parquet_urls, get_first_url,
    normalize_code,
)
from ..core.aggregator import (
    agregar_datos, agregar_expresion, get_columns,
    get_value_labels, get_var_descriptions, get_var_types,
    resumen_nacional, resumen_expresion,
)

# Mapeo del 'tipo' del diccionario al tipo interno usado por el panel.
TIPO_MAP = {"categorica": "categorical",
            "texto": "categorical", "numerica": "numeric"}

# Abreviatura del tipo para mostrar junto al nombre de la variable.
TIPO_ABBR = {"categorica": "cat", "numerica": "num", "texto": "txt"}

DEPTOS = [
    ("Todos los departamentos", None),
    ("Chuquisaca (01)", "01"), ("La Paz (02)", "02"),
    ("Cochabamba (03)", "03"), ("Oruro (04)", "04"),
    ("Potosí (05)", "05"),     ("Tarija (06)", "06"),
    ("Santa Cruz (07)", "07"), ("Beni (08)", "08"),
    ("Pando (09)", "09"),
]

# Etiqueta visible del combo de nivel → nivel interno del motor.
NIVELES = [
    ("Departamental",     "departamento"),
    ("Municipal",         "municipio"),
    ("Manzano/Comunidad", "unidad"),
]

AREAS_OPCIONES = [
    ("Urbana y rural",              None),
    ("Solo manzanos (urbana)",      "urbana"),
    ("Solo comunidades (rural)",    "rural"),
]

# Cómo se nombra la unidad de análisis de cada nivel en los mensajes y el resumen.
UNIDAD_SG = {"departamento": "departamento", "municipio": "municipio",
             "unidad": "manzano/comunidad"}
UNIDAD_PL = {"departamento": "departamentos", "municipio": "municipios",
             "unidad": "manzanos y comunidades"}

GEO_COLS = {"idep", "iprov", "imun", "i00", "area_cod", "ubigeo",
            "dpto", "dep", "departamento", "cod_dep", "depto",
            "mun", "municipio", "cod_mun", "comun"}


def _plugin_version():
    """Lee version= de metadata.txt (fuente de verdad). '' si no se puede leer."""
    meta = os.path.join(os.path.dirname(
        os.path.dirname(__file__)), "metadata.txt")
    try:
        with open(meta, encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("version="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        pass
    return ""


def _is_geo_or_technical(col):
    """Columnas a ocultar del selector de variables: geográficas y técnicas
    (claves de join *_REF_ID, REDCODEN) que no son variables de análisis."""
    c = col.lower()
    return (c in GEO_COLS
            or c.endswith("_ref_id")
            or c in ("redcoden",))


VAR_LABELS = {
    "p25_sexo": "Sexo", "p26_edad": "Edad en años",
    "area": "Área (urbana/rural)",
    "p27_autoidenp": "Autoidentificación pueblos indígenas",
    "p28_idiomahab": "Idioma o lengua habitual",
    "p29_idiomamat": "Idioma o lengua materna",
    "p34_nivelinst": "Nivel de instrucción",
    "p35_gradoaprob": "Grado aprobado",
    "p43_condact": "Condición de actividad",
    "p44_ocup": "Ocupación principal",
    "p14_discap": "Tiene discapacidad",
    "p08_migracion": "Migración (5 años)",
    "v03_tipoviv": "Tipo de vivienda",
    "v08_aguared": "Agua por cañería de red",
    "v16_energelec": "Energía eléctrica",
    "v25_tenencia": "Tenencia de la vivienda",
    "sexo": "Sexo", "edad": "Edad",
    "p02_sexo": "Sexo", "p03_edadanios": "Edad en años",
    "p14_nivinstru": "Nivel de instrucción",
}


# ─────────────────────────────────────────────────────────────────────────────
# Workers
# ─────────────────────────────────────────────────────────────────────────────

class InstallWorker(QThread):
    status = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def run(self):
        install_duckdb(
            status_cb=lambda m: self.status.emit(m),
            done_cb=lambda ok, msg="": self.finished.emit(ok, msg),
        )


class ColumnsWorker(QThread):
    # columns, {var: desc}, {var: tipo}
    finished = pyqtSignal(list, dict, dict)

    def __init__(self, path_or_url, anio, tabla=None, remote=False):
        super().__init__()
        self.path_or_url = path_or_url
        self.anio = anio
        self.tabla = tabla
        self.remote = remote

    def run(self):
        try:
            cols = get_columns(self.path_or_url, self.remote)
            descs = get_var_descriptions(self.anio, self.tabla)
            types = get_var_types(self.anio, self.tabla)
            self.finished.emit(cols, descs, types)
        except Exception:
            self.finished.emit([], {}, {})


class MapWorker(QThread):
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, anio, tabla, nivel, variable, agg, category,
                 departamento=None, sql_expr=None, municipio=None, area=None):
        super().__init__()
        self.anio = anio
        self.tabla = tabla
        self.nivel = nivel
        self.variable = variable
        self.agg = agg
        self.category = category
        self.departamento = departamento
        self.sql_expr = sql_expr
        self.municipio = municipio
        self.area = area

    def run(self):
        try:
            urls = get_parquet_urls(self.anio, self.tabla, self.departamento)
            unidad = UNIDAD_SG.get(self.nivel, "municipio")

            # DuckDB consulta el parquet remoto sin descargar el archivo.
            if not duckdb_available():
                raise RuntimeError(
                    "El motor de consulta (DuckDB) aún no está disponible. "
                    "Espera a que termine de instalarse o revisa tu conexión a "
                    "internet, y vuelve a intentarlo."
                )

            # Los indicadores de ficha y el modo SQL avanzado comparten camino:
            # ambos son una expresión agregada que el motor envuelve con el
            # GROUP BY geográfico.
            if self.sql_expr:
                self.status.emit(f"Consultando GitHub (sin descarga) por {unidad}…")
                self.progress.emit(20)
                df = agregar_expresion(urls, self.nivel, self.sql_expr,
                                       departamento=self.departamento,
                                       municipio=self.municipio, area=self.area)
                self.status.emit("Calculando valor de referencia…")
                self.progress.emit(85)
                national = resumen_expresion(urls, self.sql_expr,
                                             departamento=self.departamento,
                                             municipio=self.municipio,
                                             area=self.area)
                self.progress.emit(95)
                self.finished.emit({"df": df, "national": national})
                return

            self.status.emit(
                f"Consultando GitHub (sin descarga) por {unidad}…")

            self.progress.emit(65)
            df = agregar_datos(urls, self.nivel, self.variable,
                               self.agg, self.category,
                               departamento=self.departamento,
                               municipio=self.municipio, area=self.area)

            # Valor de referencia (nacional, o del territorio filtrado)
            self.status.emit("Calculando valor de referencia…")
            self.progress.emit(85)
            national = resumen_nacional(urls, self.variable, self.agg,
                                        self.category,
                                        departamento=self.departamento,
                                        municipio=self.municipio,
                                        area=self.area)
            self.progress.emit(95)
            self.finished.emit({"df": df, "national": national})
        except Exception as exc:
            self.error.emit(str(exc))


class GeomWorker(QThread):
    """Descarga y lee las geometrías de un municipio (manzanos y/o comunidades).

    Son varios MB de parquet: si se leyeran en el hilo principal, QGIS se
    congelaría durante la descarga. Devuelve solo datos crudos (WKB); la capa se
    construye en el hilo principal, que es donde deben vivir los objetos de QGIS.
    """
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    finished = pyqtSignal(object)
    error = pyqtSignal(str)

    def __init__(self, municipio, area=None):
        super().__init__()
        self.municipio = municipio
        self.area = area

    def run(self):
        try:
            areas = [self.area] if self.area else ["urbana", "rural"]
            geoms = {}
            for i, area in enumerate(areas):
                etiqueta = "manzanos" if area == "urbana" else "comunidades"
                self.status.emit(f"Obteniendo geometrías de {etiqueta}…")
                geoms[area] = fichas.geometrias(
                    self.municipio, area,
                    progress_cb=lambda p, i=i, n=len(areas):
                        self.progress.emit(int((i + p / 100) / n * 100)),
                )
            self.finished.emit(geoms)
        except Exception as exc:
            self.error.emit(str(exc))


# ─────────────────────────────────────────────────────────────────────────────
# Panel
# ─────────────────────────────────────────────────────────────────────────────

class CensosBOPanel(QDockWidget):
    def __init__(self, iface):
        super().__init__("Q-CensosBo", iface.mainWindow())
        self.iface = iface
        self.setObjectName("CensosBOPanel")
        self.setMinimumWidth(290)
        self.setMaximumWidth(460)

        self._map_worker = None
        self._cols_worker = None
        self._install_worker = None
        self._geom_worker = None
        self._var_descriptions = {}
        self._var_types = {}                 # {variable: "categorica"|"numerica"|"texto"}
        self._current_var_type = None        # "categorical" | "numeric" | None
        # Resultado de "Consultar": (params_key, df, ctx). "Generar mapa" solo
        # dibuja la capa a partir de este resultado, sin volver a consultar.
        self._agg_result = None

        self._build_ui()
        self._apply_styles()
        self._connect_signals()
        self._update_tabla_combo()
        self._show_engine_status()

        if not duckdb_available():
            self._auto_install_duckdb()

    # ─────────────────────────── Build UI ────────────────────────────────────

    def _build_ui(self):
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setWidget(scroll)

        container = QWidget()
        container.setObjectName("censosbo_container")
        scroll.setWidget(container)

        main = QVBoxLayout(container)
        main.setContentsMargins(10, 10, 10, 10)
        main.setSpacing(8)

        # Encabezado
        lbl_title = QLabel("Q-CensosBo")
        lbl_title.setObjectName("lbl_section")
        lbl_title.setAlignment(Qt.AlignCenter)
        main.addWidget(lbl_title)

        _ver = _plugin_version()
        if _ver:
            lbl_version = QLabel(f"v{_ver}")
            lbl_version.setObjectName("lbl_hint")
            lbl_version.setAlignment(Qt.AlignCenter)
            main.addWidget(lbl_version)

        self.lbl_engine = QLabel("")
        self.lbl_engine.setObjectName("lbl_hint")
        self.lbl_engine.setAlignment(Qt.AlignCenter)
        self.lbl_engine.setWordWrap(True)
        main.addWidget(self.lbl_engine)

        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        main.addWidget(sep)

        # ── Sección: Datos ────────────────────────────────────────────────────
        grp_datos = QGroupBox("Datos")
        form_datos = QFormLayout(grp_datos)
        form_datos.setSpacing(6)
        form_datos.setContentsMargins(8, 14, 8, 8)

        self.combo_anio = QComboBox()
        self.combo_anio.addItems(["2024", "2012", "2001", "1992", "1976"])
        form_datos.addRow("Año:", self.combo_anio)

        self.combo_tabla = QComboBox()
        form_datos.addRow("Tabla:", self.combo_tabla)

        self.combo_nivel = QComboBox()
        for lbl, key in NIVELES:
            self.combo_nivel.addItem(lbl, key)
        form_datos.addRow("Nivel:", self.combo_nivel)

        self.lbl_depto = QLabel("Departamento:")
        self.combo_depto = QComboBox()
        for lbl, code in DEPTOS:
            self.combo_depto.addItem(lbl, code)
        self.lbl_depto.setVisible(False)
        self.combo_depto.setVisible(False)
        form_datos.addRow(self.lbl_depto, self.combo_depto)

        # Municipio y área solo aplican al nivel de manzano/comunidad: el país
        # entero son 268.604 unidades, así que se mapea de a un municipio.
        self.lbl_municipio = QLabel("Municipio:")
        self.combo_municipio = QComboBox()
        self.combo_municipio.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.lbl_municipio.setVisible(False)
        self.combo_municipio.setVisible(False)
        form_datos.addRow(self.lbl_municipio, self.combo_municipio)

        self.lbl_area = QLabel("Área:")
        self.combo_area = QComboBox()
        for lbl, key in AREAS_OPCIONES:
            self.combo_area.addItem(lbl, key)
        self.lbl_area.setToolTip(
            "Los manzanos urbanos son polígonos; las comunidades rurales, puntos.\n"
            "Con ambas se generan dos capas agrupadas y con la misma escala de color."
        )
        self.lbl_area.setVisible(False)
        self.combo_area.setVisible(False)
        form_datos.addRow(self.lbl_area, self.combo_area)

        main.addWidget(grp_datos)

        # ── Sección: Análisis ─────────────────────────────────────────────────
        grp_analisis = QGroupBox("Análisis")
        form_analisis = QFormLayout(grp_analisis)
        form_analisis.setSpacing(6)
        form_analisis.setContentsMargins(8, 14, 8, 8)

        self.combo_variable = QComboBox()
        self.combo_variable.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon
        )
        form_analisis.addRow("Variable:", self.combo_variable)

        self.lbl_var_desc = QLabel("")
        self.lbl_var_desc.setObjectName("lbl_var_desc")
        self.lbl_var_desc.setWordWrap(True)
        self.lbl_var_desc.setVisible(False)
        form_analisis.addRow(self.lbl_var_desc)

        # Las opciones las define _update_agg_for_type según el tipo de variable.
        self.combo_agg = QComboBox()
        form_analisis.addRow("Agregación:", self.combo_agg)

        CLASIFICACION_OPTIONS = [
            ("Natural Breaks (Jenks)", "jenks"),
            ("Cuantiles",              "quantile"),
            ("Intervalo igual",        "equal"),
            ("Desviación estándar",    "stddev"),
        ]
        self.combo_clasificacion = QComboBox()
        for lbl, key in CLASIFICACION_OPTIONS:
            self.combo_clasificacion.addItem(lbl, key)
        self._lbl_clasificacion = QLabel("Clasificación:")
        form_analisis.addRow(self._lbl_clasificacion, self.combo_clasificacion)

        self.lbl_categoria = QLabel("Categoría:")
        self.combo_categoria = QComboBox()
        self.lbl_categoria.setVisible(False)
        self.combo_categoria.setVisible(False)
        form_analisis.addRow(self.lbl_categoria, self.combo_categoria)

        self.btn_consultar = QPushButton("1 · Consultar")
        self.btn_consultar.setObjectName("btn_consultar")
        self.btn_consultar.setCursor(Qt.PointingHandCursor)
        self.btn_consultar.setToolTip(
            "Calcula la agregación por unidad geográfica y muestra el resumen abajo."
        )
        form_analisis.addRow(self.btn_consultar)

        sep_sql = QFrame()
        sep_sql.setFrameShape(QFrame.HLine)
        sep_sql.setFrameShadow(QFrame.Sunken)
        form_analisis.addRow(sep_sql)

        self.chk_avanzado = QCheckBox("Modo SQL avanzado")
        self.chk_avanzado.setToolTip(
            "Escribe tu propia fórmula DuckDB/SQL.\n"
            "El plugin agrega automáticamente el GROUP BY geográfico."
        )
        form_analisis.addRow(self.chk_avanzado)

        self.txt_sql = QPlainTextEdit()
        self.txt_sql.setPlaceholderText(
            "Escribe la expresión para el campo valor.\n"
            "Ejemplos:\n"
            "  AVG(p26_edad)\n"
            "  100.0 * SUM(CASE WHEN p25_sexo = 1 THEN 1 END) / COUNT(*)\n"
            "  SUM(CASE WHEN p26_edad >= 65 THEN 1 ELSE 0 END)\n"
            "     / NULLIF(SUM(CASE WHEN p26_edad < 15 THEN 1 ELSE 0 END), 0) * 100"
        )
        self.txt_sql.setMaximumHeight(95)
        self.txt_sql.setVisible(False)
        form_analisis.addRow(self.txt_sql)

        self.lbl_sql_hint = QLabel(
            "Disponible: GROUP BY geográfico incluido automáticamente. "
            "Usa cualquier función DuckDB (AVG, SUM, COUNT, CASE, etc.)."
        )
        self.lbl_sql_hint.setObjectName("lbl_hint")
        self.lbl_sql_hint.setWordWrap(True)
        self.lbl_sql_hint.setVisible(False)
        form_analisis.addRow(self.lbl_sql_hint)

        main.addWidget(grp_analisis)

        # ── Sección: Resumen ──────────────────────────────────────────────────
        grp_stats = QGroupBox("Resumen del resultado")
        stats_layout = QVBoxLayout(grp_stats)
        stats_layout.setSpacing(4)
        stats_layout.setContentsMargins(8, 14, 8, 8)

        row_total = QHBoxLayout()
        self.lbl_total_caption = QLabel("Unidades geográficas:")
        row_total.addWidget(self.lbl_total_caption)
        self.lbl_total = QLabel("—")
        self.lbl_total.setObjectName("lbl_stat_value")
        self.lbl_total.setAlignment(Qt.AlignRight)
        row_total.addWidget(self.lbl_total)
        stats_layout.addLayout(row_total)

        self.stats_bars_widget = QWidget()
        self.stats_bars_layout = QVBoxLayout(self.stats_bars_widget)
        self.stats_bars_layout.setSpacing(3)
        self.stats_bars_layout.setContentsMargins(0, 0, 0, 0)
        stats_layout.addWidget(self.stats_bars_widget)
        self.stats_bars_widget.setVisible(False)

        self.lbl_stats_hint = QLabel(
            "Pulsa '1 · Consultar' para calcular\nla agregación y ver el resumen.")
        self.lbl_stats_hint.setObjectName("lbl_hint")
        self.lbl_stats_hint.setAlignment(Qt.AlignCenter)
        stats_layout.addWidget(self.lbl_stats_hint)

        main.addWidget(grp_stats)

        # ── Acción ────────────────────────────────────────────────────────────
        action_w = QWidget()
        action_l = QVBoxLayout(action_w)
        action_l.setContentsMargins(0, 0, 0, 0)
        action_l.setSpacing(6)

        self.lbl_progress = QLabel("Procesando…")
        self.lbl_progress.setObjectName("lbl_hint")
        self.lbl_progress.setAlignment(Qt.AlignCenter)
        self.lbl_progress.setVisible(False)
        action_l.addWidget(self.lbl_progress)

        self.progress_bar = QProgressBar()
        self.progress_bar.setObjectName("progress_descarga")
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setVisible(False)
        action_l.addWidget(self.progress_bar)

        self.btn_generar = QPushButton("2 · Generar mapa")
        self.btn_generar.setObjectName("btn_generar")
        self.btn_generar.setCursor(Qt.PointingHandCursor)
        self.btn_generar.setEnabled(False)   # se habilita tras "Consultar"
        self.btn_generar.setToolTip(
            "Dibuja el mapa con el resultado ya consultado.")
        action_l.addWidget(self.btn_generar)

        main.addWidget(action_w)
        main.addStretch()

    def _apply_styles(self):
        qss = os.path.join(os.path.dirname(
            os.path.dirname(__file__)), "styles", "theme.qss")
        if os.path.exists(qss):
            with open(qss, encoding="utf-8") as f:
                self.widget().setStyleSheet(f.read())

    # ─────────────────────────── Engine status ───────────────────────────────

    def _show_engine_status(self):
        if duckdb_available():
            self.lbl_engine.setText("⚡ DuckDB activo")
        else:
            self.lbl_engine.setText("⏳ Preparando motor de consulta…")

    def _auto_install_duckdb(self):
        self.lbl_engine.setText("⏳ Instalando DuckDB (solo la primera vez)…")
        self.btn_generar.setEnabled(False)
        self._install_worker = InstallWorker()
        self._install_worker.status.connect(self.lbl_engine.setText)
        self._install_worker.finished.connect(self._on_install_done)
        self._install_worker.start()

    def showEvent(self, event):
        """Al (re)abrir el panel, reintenta instalar el motor si aún falta y no
        hay una instalación en curso. Da una vía de reintento sin botón extra."""
        super().showEvent(event)
        worker_running = bool(
            self._install_worker and self._install_worker.isRunning())
        if not duckdb_available() and not worker_running:
            self._auto_install_duckdb()

    def _on_install_done(self, success, message=""):
        self.btn_generar.setEnabled(True)
        if success:
            self.lbl_engine.setText(
                "⚡ DuckDB activo — consulta remota sin descarga")
            self._update_variable_combo()
        else:
            self.lbl_engine.setText(
                "⚠ " + (message or "No se pudo instalar el motor (DuckDB).")
                + " Reabre el panel para reintentar."
            )

    # ─────────────────────────── Signals ─────────────────────────────────────

    def _connect_signals(self):
        # El año repuebla las tablas y eso, en cascada, revisa niveles y variables.
        self.combo_anio.currentIndexChanged.connect(self._update_tabla_combo)
        self.combo_tabla.currentIndexChanged.connect(self._on_tabla_changed)
        self.combo_nivel.currentIndexChanged.connect(self._on_nivel_changed)
        self.combo_variable.currentIndexChanged.connect(
            self._on_variable_changed)
        self.combo_agg.currentIndexChanged.connect(self._on_agg_changed)
        self.combo_depto.currentIndexChanged.connect(self._on_depto_changed)
        self.combo_municipio.currentIndexChanged.connect(self._invalidate_result)
        self.combo_area.currentIndexChanged.connect(self._invalidate_result)
        self.combo_categoria.currentIndexChanged.connect(
            self._invalidate_result)
        self.txt_sql.textChanged.connect(self._invalidate_result)
        self.chk_avanzado.toggled.connect(self._on_avanzado_toggled)
        self.btn_consultar.clicked.connect(self._on_consultar_clicked)
        self.btn_generar.clicked.connect(self._on_generar_clicked)

    # ─────────────────────────── Slots ───────────────────────────────────────

    def _update_tabla_combo(self):
        anio = int(self.combo_anio.currentText())
        self.combo_tabla.blockSignals(True)
        self.combo_tabla.clear()
        for lbl, key in get_tables_for_year(anio):
            self.combo_tabla.addItem(lbl, key)
        self.combo_tabla.blockSignals(False)
        self._on_tabla_changed()

    def _es_ficha(self):
        """True si la tabla elegida es una de las de manzano/comunidad."""
        return self.combo_tabla.currentData() in fichas.TABLAS

    def _nivel(self):
        return self.combo_nivel.currentData() or "departamento"

    def _on_tabla_changed(self):
        self._update_nivel_availability()
        self._update_variable_combo()
        self._on_nivel_changed()

    def _on_nivel_changed(self):
        nivel = self._nivel()
        es_unidad = (nivel == "unidad")
        muestra_depto = nivel in ("municipio", "unidad")
        self.lbl_depto.setVisible(muestra_depto)
        self.combo_depto.setVisible(muestra_depto)
        self.lbl_municipio.setVisible(es_unidad)
        self.combo_municipio.setVisible(es_unidad)
        self.lbl_area.setVisible(es_unidad and self._es_ficha())
        self.combo_area.setVisible(es_unidad and self._es_ficha())
        if es_unidad:
            self._update_municipio_combo()
        self._invalidate_result()

    def _on_depto_changed(self):
        if self._nivel() == "unidad":
            self._update_municipio_combo()
        self._invalidate_result()

    def _update_municipio_combo(self):
        """Llena el selector de municipio con los del departamento elegido."""
        from ..core.layer_builder import municipios_por_depto

        idep = self.combo_depto.currentData()
        prev = self.combo_municipio.currentData()
        self.combo_municipio.blockSignals(True)
        self.combo_municipio.clear()
        if not idep:
            self.combo_municipio.addItem("(Elige un departamento)", None)
        else:
            for nombre, code in municipios_por_depto(idep):
                self.combo_municipio.addItem(f"{nombre} ({code})", code)
        idx = self.combo_municipio.findData(prev) if prev else -1
        self.combo_municipio.setCurrentIndex(idx if idx >= 0 else 0)
        self.combo_municipio.blockSignals(False)

    def _update_nivel_availability(self):
        """Habilita cada nivel según el censo y la tabla.

        - 1976 no tiene nivel municipal (su unidad menor es el cantón).
        - El nivel de manzano/comunidad solo existe en las tablas de fichas del
          CPV-2024; en los microdatos la unidad es la persona o la vivienda.
        """
        anio = int(self.combo_anio.currentText())
        reglas = {
            "municipio": (
                anio != 1976,
                "El censo 1976 solo está disponible a nivel departamental.",
            ),
            "unidad": (
                self._es_ficha(),
                "El nivel de manzano/comunidad solo está en las tablas de fichas "
                "del CPV-2024 (elige «Ficha de indicadores» o «Unidades censales»).",
            ),
        }
        avisos = []
        for nivel, (habilitado, motivo) in reglas.items():
            idx = self.combo_nivel.findData(nivel)
            if idx < 0:
                continue
            self.combo_nivel.model().item(idx).setEnabled(habilitado)
            if habilitado:
                continue
            avisos.append(motivo)
            if self._nivel() == nivel:
                self.combo_nivel.setCurrentIndex(
                    self.combo_nivel.findData("departamento"))
        self.combo_nivel.setToolTip("\n".join(avisos))

    def _refresh_categoria_visibility(self):
        show = (
            self._current_var_type == "categorical"
            and self.combo_agg.currentData() == "pct_category"
            and not self.chk_avanzado.isChecked()
            and self.combo_categoria.count() > 0   # poblado tras "Consultar"
        )
        self.lbl_categoria.setVisible(show)
        self.combo_categoria.setVisible(show)

    def _on_agg_changed(self):
        # "Porcentaje de una categoría" necesita el selector de categoría,
        # poblado del diccionario (no requiere consulta).
        if (self._current_var_type == "categorical"
                and self.combo_agg.currentData() == "pct_category"
                and self.combo_categoria.count() == 0):
            self._populate_categorias(self.combo_variable.currentData() or "")
        self._refresh_categoria_visibility()
        self._invalidate_result()

    def _invalidate_result(self):
        """El resultado de Consultar dejó de ser válido: hay que volver a consultar."""
        self._agg_result = None
        self.btn_generar.setEnabled(False)

    def _geo_filtros(self, nivel):
        """(departamento, municipio, area) según el nivel y los combos visibles."""
        depto = (self.combo_depto.currentData()
                 if nivel in ("municipio", "unidad") else None)
        municipio = self.combo_municipio.currentData() if nivel == "unidad" else None
        area = (self.combo_area.currentData()
                if nivel == "unidad" and self._es_ficha() else None)
        return depto, municipio, area

    def _params_key(self):
        """Clave de los parámetros que afectan la agregación (no el estilo del mapa)."""
        anio = int(self.combo_anio.currentText())
        tabla = self.combo_tabla.currentData()
        nivel = self._nivel()
        geo = self._geo_filtros(nivel)
        if self.chk_avanzado.isChecked():
            return (anio, tabla, nivel) + geo + (
                "__sql__", self.txt_sql.toPlainText().strip())
        variable = self.combo_variable.currentData() or "__count__"
        agg = self.combo_agg.currentData() or "__count__"
        category = self.combo_categoria.currentData() if agg == "pct_category" else None
        return (anio, tabla, nivel) + geo + (variable, agg, category)

    def _on_consultar_clicked(self):
        """Ejecuta la agregación completa y muestra el resumen. NO dibuja el mapa."""
        anio = int(self.combo_anio.currentText())
        tabla = self.combo_tabla.currentData()
        if not tabla:
            self.iface.messageBar().pushWarning("Q-CensosBo", "Selecciona una tabla.")
            return
        nivel = self._nivel()
        depto, municipio, area = self._geo_filtros(nivel)

        if nivel == "unidad" and not municipio:
            self.iface.messageBar().pushWarning(
                "Q-CensosBo",
                "Elige departamento y municipio: el nivel de manzano/comunidad "
                "se mapea de a un municipio (el país son 268.604 unidades).")
            return

        variable = self.combo_variable.currentData() or "__count__"
        agg = self.combo_agg.currentData() or "__count__"
        category = None
        sql_expr = None

        if self.chk_avanzado.isChecked():
            sql_expr = self.txt_sql.toPlainText().strip()
            if not sql_expr:
                self.iface.messageBar().pushWarning("Q-CensosBo", "Escribe una expresión SQL.")
                return
            if not duckdb_available():
                self.iface.messageBar().pushWarning(
                    "Q-CensosBo", "El modo SQL avanzado requiere DuckDB.")
                return
        else:
            if not variable or variable in ("__loading__", "__error__"):
                self.iface.messageBar().pushWarning("Q-CensosBo", "Selecciona una variable.")
                return
            if agg == "pct_category":
                category = self.combo_categoria.currentData()
                if not category:
                    self.iface.messageBar().pushWarning(
                        "Q-CensosBo", "Elige una categoría para calcular el porcentaje.")
                    return
            if self._es_ficha():
                # Los indicadores de ficha son conteos ya agregados: se declaran
                # como expresión SQL (ver fichas.sql_valor) en vez de pasar por
                # las agregaciones de microdato.
                try:
                    sql_expr = fichas.sql_valor(tabla, variable, agg)
                except ValueError as exc:
                    self.iface.messageBar().pushWarning("Q-CensosBo", str(exc))
                    return

        if self._map_worker and self._map_worker.isRunning():
            return

        ctx = dict(anio=anio, tabla=tabla, nivel=nivel, depto=depto,
                   municipio=municipio, area=area,
                   variable=variable, agg=agg, category=category,
                   sql_expr=sql_expr, key=self._params_key(),
                   # Fichas y modo avanzado comparten el camino de expresión SQL,
                   # pero se presentan distinto: esto distingue quién la escribió.
                   sql_libre=self.chk_avanzado.isChecked())
        self._set_consulta_busy(True)
        self._map_worker = MapWorker(anio, tabla, nivel, variable, agg, category,
                                     depto, sql_expr=sql_expr,
                                     municipio=municipio, area=area)
        self._map_worker.progress.connect(self.progress_bar.setValue)
        self._map_worker.status.connect(self.lbl_progress.setText)
        self._map_worker.finished.connect(
            lambda df: self._on_aggregation_ready(df, ctx))
        self._map_worker.error.connect(self._on_consulta_error)
        self._map_worker.start()

    def _set_consulta_busy(self, active):
        self.btn_consultar.setEnabled(not active)
        self.btn_consultar.setText(
            "Consultando…" if active else "1 · Consultar")
        self.progress_bar.setVisible(active)
        self.lbl_progress.setVisible(active)
        if active:
            self.btn_generar.setEnabled(False)
            self.progress_bar.setValue(0)
            self.lbl_progress.setText("Calculando agregación…")

    def _on_aggregation_ready(self, result, ctx):
        self._set_consulta_busy(False)
        df = result.get("df") if isinstance(result, dict) else result
        if df is None or len(df) == 0:
            self._invalidate_result()
            self._show_stats_hint("La consulta no devolvió datos.")
            return
        self._agg_result = (ctx["key"], df, ctx)
        self._show_result_summary(result, ctx)
        self.btn_generar.setEnabled(True)

    def _on_consulta_error(self, msg):
        self._set_consulta_busy(False)
        self._invalidate_result()
        self.iface.messageBar().pushCritical("Q-CensosBo", msg)
        self._show_stats_hint("Error en la consulta. Revisa los parámetros.")

    def _populate_categorias(self, variable):
        """Llena el selector de categoría con las etiquetas del diccionario."""
        if not variable or variable in ("__count__", "__loading__", "__error__"):
            return
        anio = int(self.combo_anio.currentText())
        tabla = self.combo_tabla.currentData() or ""
        labels = get_value_labels(anio, variable, tabla)

        def sort_key(code):
            c = normalize_code(code)
            return (0, int(c)) if c.lstrip("-").isdigit() else (1, str(code))
        prev = self.combo_categoria.currentData()
        self.combo_categoria.blockSignals(True)
        self.combo_categoria.clear()
        for code in sorted(labels.keys(), key=sort_key):
            self.combo_categoria.addItem(f"{code} — {labels[code]}", code)
        idx = self.combo_categoria.findData(prev) if prev else -1
        self.combo_categoria.setCurrentIndex(idx if idx >= 0 else 0)
        self.combo_categoria.blockSignals(False)

    def _on_avanzado_toggled(self, checked):
        self.txt_sql.setVisible(checked)
        self.lbl_sql_hint.setVisible(checked)
        for w in (self.combo_variable, self.combo_agg):
            w.setVisible(not checked)
        # Clasificación: solo si no es modo avanzado Y la variable es numérica
        clas_visible = (not checked) and (
            self._current_var_type != "categorical")
        self.combo_clasificacion.setVisible(clas_visible)
        self._lbl_clasificacion.setVisible(clas_visible)
        # Categoría: solo si no es modo avanzado Y agg == pct_category
        if checked:
            self.lbl_categoria.setVisible(False)
            self.combo_categoria.setVisible(False)
        else:
            self._refresh_categoria_visibility()
        # Ocultar/mostrar labels de variable y agg en el formulario
        form = self.combo_variable.parent().layout()
        if form:
            for i in range(form.rowCount()):
                lbl = form.itemAt(i, form.LabelRole)
                fld = form.itemAt(i, form.FieldRole)
                if fld and fld.widget() in (self.combo_variable, self.combo_agg):
                    if lbl and lbl.widget():
                        lbl.widget().setVisible(not checked)
        self._invalidate_result()

    def _on_variable_changed(self):
        var = self.combo_variable.currentData() or ""
        desc = self._var_descriptions.get(var) or VAR_LABELS.get(var)
        if desc and var not in ("__count__", "__loading__", "__error__"):
            self.lbl_var_desc.setText(desc)
            self.lbl_var_desc.setVisible(True)
        else:
            self.lbl_var_desc.setVisible(False)

        self._invalidate_result()

        # Limpiar categorías de la variable anterior
        self.combo_categoria.blockSignals(True)
        self.combo_categoria.clear()
        self.combo_categoria.blockSignals(False)

        # El tipo viene del diccionario: ajustar la agregación ya, sin esperar
        # a "Consultar". Si el diccionario no lo trae, se decidirá al consultar.
        if self._es_ficha():
            tipo = "ficha"
        else:
            tipo = TIPO_MAP.get((self._var_types.get(var) or "").lower())
        if tipo and var not in ("__count__", "__loading__", "__error__"):
            self._current_var_type = None  # forzar refresco del combo de agregación
            self._update_agg_for_type(tipo)
            # Para categóricas, poblar el selector de categoría desde el diccionario
            if tipo == "categorical":
                self._populate_categorias(var)
                self._refresh_categoria_visibility()
        else:
            self._current_var_type = None
            self._refresh_categoria_visibility()

        self._show_stats_hint(
            "Pulsa '1 · Consultar' para calcular\nla agregación y ver el resumen.")

    def _update_agg_for_type(self, var_type):
        """Filtra el combo de agregación y muestra/oculta clasificación según tipo."""
        if var_type == self._current_var_type:
            return
        self._current_var_type = var_type

        current = self.combo_agg.currentData()
        self.combo_agg.blockSignals(True)
        self.combo_agg.clear()

        if var_type == "ficha":
            # Los indicadores de ficha ya son conteos: se suman, o se dividen
            # entre el total de su bloque para obtener la proporción.
            options = [("Total (conteo)", "total")]
            if fichas.tiene_porcentaje(self.combo_tabla.currentData(),
                                       self.combo_variable.currentData() or ""):
                options.append(("% del total del bloque", "porcentaje"))
        elif var_type == "categorical":
            options = [
                ("Porcentaje", "pct_category"),
                ("Moda",       "mode"),
            ]
        # numeric — solo resúmenes del valor (Conteo cuenta población, no el valor)
        else:
            options = [
                ("Media",               "mean"),
                ("Mediana",             "median"),
                ("Suma",                "sum"),
                ("Desviación estándar", "std"),
            ]

        for lbl, key in options:
            self.combo_agg.addItem(lbl, key)

        idx = self.combo_agg.findData(current)
        self.combo_agg.setCurrentIndex(idx if idx >= 0 else 0)
        self.combo_agg.blockSignals(False)

        # Clasificación solo aplica para mapas numéricos graduados
        show_clas = (var_type != "categorical")
        self.combo_clasificacion.setVisible(show_clas)
        self._lbl_clasificacion.setVisible(show_clas)

        # Categoría: solo visible cuando agg == pct_category
        self._refresh_categoria_visibility()

    # ─────────────────────────── Variable combo ──────────────────────────────

    def _update_variable_combo(self):
        anio = int(self.combo_anio.currentText())
        tabla = self.combo_tabla.currentData() or ""

        # Las fichas traen su catálogo empaquetado con el plugin: no hace falta
        # leer el schema remoto ni descargar diccionario, así que se llena ya.
        if tabla in fichas.TABLAS:
            self._populate_variables_ficha(tabla)
            return

        self.combo_variable.blockSignals(True)
        self.combo_variable.clear()
        if duckdb_available():
            self.combo_variable.addItem("(Cargando variables…)", "__loading__")
        else:
            # El motor aún se está instalando/preparando: no hay worker que lanzar.
            # _on_install_done vuelve a llamar a este método cuando esté listo.
            self.combo_variable.addItem(
                "(Esperando al motor de consulta…)", "__loading__")
        self.combo_variable.blockSignals(False)

        if duckdb_available():
            self._start_cols_worker(get_first_url(anio, tabla), remote=True)

    def _populate_variables_ficha(self, tabla):
        """Llena el selector con el catálogo de indicadores de las fichas.

        Van agrupados por bloque temático ("Servicios básicos · Agua por cañería
        de red") porque son 245 opciones: sin el bloque delante no se encuentran.
        """
        catalogo = fichas.catalogo(tabla)
        self._var_descriptions = {r["variable"]: r["etiqueta"] for r in catalogo}
        self._var_types = {r["variable"]: r["tipo"] for r in catalogo}

        current = self.combo_variable.currentData()
        self.combo_variable.blockSignals(True)
        self.combo_variable.clear()
        if not catalogo:
            self.combo_variable.addItem(
                "⚠ Falta el catálogo de indicadores (data/dicc_fichas.csv)",
                "__error__")
        for r in catalogo:
            bloque = fichas.BLOQUES.get(r["bloque"], r["bloque"])
            self.combo_variable.addItem(f"{bloque} · {r['etiqueta']}", r["variable"])
        idx = self.combo_variable.findData(current)
        if idx >= 0:
            self.combo_variable.setCurrentIndex(idx)
        self.combo_variable.blockSignals(False)
        self._on_variable_changed()

    def _start_cols_worker(self, path_or_url, remote):
        if self._cols_worker and self._cols_worker.isRunning():
            self._cols_worker.quit()
            self._cols_worker.wait(300)

        anio = int(self.combo_anio.currentText())
        tabla = self.combo_tabla.currentData() or ""
        w = ColumnsWorker(path_or_url, anio=anio, tabla=tabla, remote=remote)
        w.finished.connect(
            lambda cols, descs, types: self._on_columns_loaded(
                cols, descs, types, anio, tabla)
        )
        self._cols_worker = w
        w.start()

    def _on_columns_loaded(self, columns, descriptions, types, anio, tabla):
        if int(self.combo_anio.currentText()) != anio:
            return
        if self.combo_tabla.currentData() != tabla:
            return

        self._var_descriptions = descriptions
        self._var_types = types

        # Columnas vacías = el worker falló (un parquet real siempre tiene
        # columnas): casi siempre la lectura remota del schema no llegó a GitHub
        # (sin internet, proxy/firewall, o timeout). Mostramos el error en el
        # propio combo en vez de dejarlo en "Cargando…" o vacío y mudo.
        if not columns:
            self.combo_variable.blockSignals(True)
            self.combo_variable.clear()
            self.combo_variable.addItem(
                "⚠ No se pudieron cargar las variables — revisa tu conexión",
                "__error__")
            self.combo_variable.blockSignals(False)
            self._on_variable_changed()
            return

        current = self.combo_variable.currentData()
        self.combo_variable.blockSignals(True)
        self.combo_variable.clear()
        for col in columns:
            if not _is_geo_or_technical(col):
                # Priorizar descripción del codebook, luego VAR_LABELS, luego solo el nombre
                desc = descriptions.get(col) or VAR_LABELS.get(col)
                abbr = TIPO_ABBR.get((types.get(col) or "").lower())
                name = f"{col} ({abbr})" if abbr else col
                label = f"{name} — {desc}" if desc else name
                self.combo_variable.addItem(label, col)

        idx = self.combo_variable.findData(current)
        if idx >= 0:
            self.combo_variable.setCurrentIndex(idx)
        self.combo_variable.blockSignals(False)
        self._on_variable_changed()

    # ─────────────────────────── Resumen del resultado ───────────────────────

    def _show_stats_hint(self, text):
        self._clear_stat_bars()
        self.lbl_total.setText("—")
        self.lbl_total_caption.setText("Unidades geográficas:")
        self.lbl_stats_hint.setText(text)
        self.lbl_stats_hint.setVisible(True)
        self.stats_bars_widget.setVisible(False)
        self.btn_consultar.setEnabled(True)
        self.btn_consultar.setText("1 · Consultar")

    def _clear_stat_bars(self):
        while self.stats_bars_layout.count():
            item = self.stats_bars_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def _show_result_summary(self, result, ctx):
        """Resumen compacto del RESULTADO: qué se mapea, valor nacional y la
        distribución por unidad (la barra ya comunica el rango/spread)."""
        import pandas as pd
        df = result.get("df") if isinstance(result, dict) else result
        national = result.get("national") if isinstance(result, dict) else None

        self._clear_stat_bars()
        self.lbl_stats_hint.setVisible(False)
        self.stats_bars_widget.setVisible(True)

        nivel = ctx["nivel"]
        agg = ctx["agg"]
        sql_libre = ctx.get("sql_libre")
        unidad_sg = UNIDAD_SG.get(nivel, "municipio")
        unidad_pl = UNIDAD_PL.get(nivel, "municipios")
        self.lbl_total_caption.setText(f"Unidades ({unidad_pl}):")
        self.lbl_total.setText(str(len(df)))

        # Qué se está mapeando + valor de referencia (las dos líneas clave).
        # La referencia se acota al territorio filtrado (municipio o depto).
        self._add_note(self._indicator_title(ctx))
        natlbl = self._national_label(ctx, national)
        if natlbl is not None:
            if ctx.get("municipio"):
                ref_caption = "Municipal"
            elif ctx.get("depto"):
                ref_caption = "Departamental"
            else:
                ref_caption = "Nacional"
            self._add_kv_row(ref_caption, natlbl)

        if ctx["tabla"] == "fichas":
            self._add_note(
                "El INE reserva la ficha de las unidades con poca población: las "
                "que no la tienen salen sin dato en el mapa (a nivel nacional, "
                "las fichas cubren el 92 % de la población)."
            )

        pct = agg in ("pct_category", "porcentaje")

        def fmt(x):
            return f"{x:.1f}%" if pct else f"{x:,.2f}".rstrip("0").rstrip(".")

        if agg == "mode" and not sql_libre:
            # Categórico: cuántas unidades tiene cada categoría modal
            self._add_section(f"Categoría modal por {unidad_sg}")
            labels = get_value_labels(
                ctx["anio"], ctx["variable"], ctx["tabla"])
            norm = {normalize_code(k): v for k, v in labels.items()}
            counts = df["valor"].astype(str).value_counts()
            total = len(df) or 1
            for val, n in list(counts.items())[:12]:
                lbl = norm.get(normalize_code(val))
                disp = f"{val} — {lbl}" if lbl else str(val)
                self._add_stat_bar(disp, int(n / total * 100), text=f"{n}")
        else:
            vals = pd.to_numeric(df["valor"], errors="coerce").dropna()
            if len(vals) == 0:
                self._show_stats_hint(
                    "El resultado no tiene valores numéricos.")
                return
            # Ranking legible por nombre cuando las unidades caben (cualquier
            # departamento, o los municipios de uno: ≤ ~120). Histograma cuando
            # son demasiadas para listar (p. ej. todos los municipios del país,
            # 339) y siempre a nivel de manzano/comunidad, donde el nombre no
            # identifica la unidad y un ranking de códigos no diría nada.
            if len(vals) > 120 or nivel == "unidad":
                import numpy as np
                self._add_section(f"Distribución de los {unidad_pl}")
                method = self.combo_clasificacion.currentData() or "quantile"
                edges = self._value_bins(vals.values, method, k=5)
                hist, _ = np.histogram(vals.values, bins=edges)
                mxc = int(hist.max()) or 1
                for i, c in enumerate(hist):
                    rng = f"{fmt(edges[i])} – {fmt(edges[i + 1])}"
                    self._add_stat_bar(
                        rng, int(c / mxc * 100), text=str(int(c)))
            else:
                self._add_section(f"Por {unidad_sg} (mayor → menor)")
                vmin = float(vals.min())
                rng = (float(vals.max()) - vmin) or 1
                tmp = df.assign(_v=pd.to_numeric(
                    df["valor"], errors="coerce")).dropna(subset=["_v"])
                shown = tmp.sort_values("_v", ascending=False).head(15)
                for _, r in shown.iterrows():
                    name = str(r.get("geo_nombre", r["geo_code"]))
                    fill = min(100, int((r["_v"] - vmin) / rng * 96) + 4)
                    self._add_stat_bar(f"{name}  ·  {fmt(r['_v'])}", fill)
                if len(tmp) > len(shown):
                    self._add_note(f"(mostrando {len(shown)} de {len(tmp)})")

    # ── Helpers del resumen ───────────────────────────────────────────────────

    def _indicator_title(self, ctx):
        nivel = ctx["nivel"]
        unidad = UNIDAD_SG.get(nivel, "municipio")
        if ctx.get("sql_libre"):
            return f"Expresión SQL — por {unidad}"
        var = ctx["variable"]
        agg = ctx["agg"]
        if ctx["tabla"] in fichas.TABLAS:
            etiqueta = self._var_descriptions.get(var, var)
            prefijo = "% de" if agg == "porcentaje" else "Total de"
            return f"{prefijo} {etiqueta} — por {unidad}"
        templ = {
            "mean":   f"Media de {var}",
            "median": f"Mediana de {var}",
            "sum":    f"Suma de {var}",
            "std":    f"Desv. estándar de {var}",
            "__count__": "Conteo de registros",
            "mode":   f"Categoría más frecuente de {var}",
        }
        if agg == "pct_category":
            cat = self.combo_categoria.currentText() or str(ctx["category"])
            metric = f"% con «{cat}» de {var}"
        else:
            metric = templ.get(agg, var)
        return f"{metric} — por {unidad}"

    def _national_label(self, ctx, national):
        if national is None:
            return None
        agg = ctx["agg"]
        try:
            if agg in ("pct_category", "porcentaje"):
                return f"{float(national):.1f}%"
            if agg == "total":
                return f"{float(national):,.0f}".replace(",", ".")
            if agg == "__count__":
                return f"{int(national):,}".replace(",", ".")
            if agg == "mode":
                labels = get_value_labels(
                    ctx["anio"], ctx["variable"], ctx["tabla"])
                norm = {normalize_code(k): v for k, v in labels.items()}
                code = str(national)
                lbl = norm.get(normalize_code(code))
                return f"{code} — {lbl}" if lbl else code
            return f"{float(national):,.2f}".rstrip("0").rstrip(".")
        except Exception:
            return str(national)

    def _value_bins(self, values, method, k=5):
        """Cortes de clase para el histograma. 'equal' y 'quantile' exactos;
        'jenks'/'stddev' usan cuantiles como aproximación para esta vista previa."""
        import numpy as np
        v = np.asarray(values, dtype=float)
        vmin, vmax = float(v.min()), float(v.max())
        if vmin == vmax:
            return [vmin, vmax + 1]
        if method == "equal":
            return list(np.linspace(vmin, vmax, k + 1))
        edges = sorted(set(np.quantile(v, np.linspace(0, 1, k + 1)).tolist()))
        return edges if len(edges) >= 2 else [vmin, vmax]

    def _add_section(self, text):
        lbl = QLabel(text)
        lbl.setObjectName("lbl_section")
        self.stats_bars_layout.addWidget(lbl)

    def _add_note(self, text):
        lbl = QLabel(text)
        lbl.setObjectName("lbl_var_desc")
        lbl.setWordWrap(True)
        self.stats_bars_layout.addWidget(lbl)

    def _add_kv_row(self, caption, value):
        row_w = QWidget()
        row_l = QHBoxLayout(row_w)
        row_l.setContentsMargins(0, 0, 0, 0)
        lk = QLabel(caption + ":")
        lk.setFixedWidth(70)
        lv = QLabel(str(value))
        lv.setObjectName("lbl_stat_value")
        row_l.addWidget(lk)
        row_l.addWidget(lv)
        row_l.addStretch()
        self.stats_bars_layout.addWidget(row_w)

    def _add_stat_bar(self, label, fill, text=""):
        row_w = QWidget()
        row_l = QHBoxLayout(row_w)
        row_l.setContentsMargins(0, 0, 0, 0)
        row_l.setSpacing(4)
        lbl = QLabel(str(label))
        lbl.setFixedWidth(120)
        lbl.setWordWrap(True)
        lbl.setToolTip(str(label))
        bar = QProgressBar()
        bar.setObjectName("stat_bar")
        bar.setRange(0, 100)
        bar.setValue(max(0, min(100, int(fill))))
        bar.setFormat(str(text))
        bar.setTextVisible(bool(text))
        bar.setFixedHeight(16)
        row_l.addWidget(lbl)
        row_l.addWidget(bar)
        self.stats_bars_layout.addWidget(row_w)

    # ─────────────────────────── Generar mapa ────────────────────────────────

    def _on_generar_clicked(self):
        """Dibuja el mapa con el resultado ya consultado. No vuelve a consultar."""
        key = self._params_key()
        if not self._agg_result or self._agg_result[0] != key:
            self.iface.messageBar().pushWarning(
                "Q-CensosBo",
                "Pulsa '1 · Consultar' primero (o los parámetros cambiaron).")
            return
        _, df, ctx = self._agg_result
        clasificacion = self.combo_clasificacion.currentData() or "jenks"
        self._build_layer(df, ctx, clasificacion)

    def _nombre_capa(self, ctx):
        """Nombre de la capa: identifica de un vistazo qué mapa es cada una."""
        agg = ctx["agg"]
        variable = ctx["variable"]
        if ctx.get("sql_libre"):
            base = "sql"
        else:
            agg_tag = {
                "__count__": "cnt", "mean": "avg", "median": "med",
                "sum": "sum", "std": "std", "mode": "mod",
                "pct_category": "pct", "total": "tot", "porcentaje": "pct",
            }.get(agg, agg)
            base = f"{(variable or 'var')[:14]}_{agg_tag}"
        geo_tag = ""
        if ctx.get("municipio"):
            geo_tag = f"_mun{ctx['municipio']}"
        elif ctx.get("depto"):
            geo_tag = f"_dep{ctx['depto']}"
        area_tag = f"_{ctx['area']}" if ctx.get("area") else ""
        return (f"{ctx['tabla']}_{ctx['anio']}_{ctx['nivel'][:4]}"
                f"{geo_tag}{area_tag}_{base}")

    def _build_layer(self, df, ctx, clasificacion):
        from ..core.layer_builder import crear_capa
        agg = ctx["agg"]
        variable = ctx["variable"]
        nivel = ctx["nivel"]
        anio = ctx["anio"]
        tabla = ctx["tabla"]
        departamento = ctx["depto"]

        # Nivel manzano/comunidad: las geometrías vienen del release de fichas y
        # hay que descargarlas, así que van en un hilo aparte.
        if nivel == "unidad":
            self._start_geom_worker(df, ctx, clasificacion)
            return

        try:
            # Solo el mapa de Moda es categórico (colores por categoría). El
            # porcentaje y las agregaciones numéricas son mapas graduados.
            is_categorical = (agg == "mode") and not ctx.get("sql_libre")

            value_labels = None
            if is_categorical and variable not in ("__count__", "__loading__", "__error__"):
                try:
                    value_labels = get_value_labels(anio, variable, tabla)
                except Exception:
                    value_labels = None

            crear_capa(df, nivel, self._nombre_capa(ctx), self.iface,
                       departamento=departamento,
                       is_categorical=is_categorical,
                       clasificacion=clasificacion,
                       value_labels=value_labels)
            self.iface.messageBar().pushSuccess(
                "Q-CensosBo", f"Mapa generado: {len(df)} unidades geográficas."
            )
        except FileNotFoundError as exc:
            self.iface.messageBar().pushCritical("Q-CensosBo", str(exc))
        except Exception as exc:
            self.iface.messageBar().pushCritical("Q-CensosBo", f"Error: {exc}")

    def _start_geom_worker(self, df, ctx, clasificacion):
        """Descarga las geometrías del municipio y luego dibuja la(s) capa(s)."""
        if self._geom_worker and self._geom_worker.isRunning():
            return
        self._set_generar_busy(True)
        w = GeomWorker(ctx["municipio"], ctx.get("area"))
        w.progress.connect(self.progress_bar.setValue)
        w.status.connect(self.lbl_progress.setText)
        w.finished.connect(
            lambda geoms: self._on_geoms_ready(geoms, df, ctx, clasificacion))
        w.error.connect(self._on_geoms_error)
        self._geom_worker = w
        w.start()

    def _set_generar_busy(self, active):
        self.btn_generar.setEnabled(not active)
        self.btn_generar.setText("Generando…" if active else "2 · Generar mapa")
        self.progress_bar.setVisible(active)
        self.lbl_progress.setVisible(active)
        if active:
            self.progress_bar.setValue(0)
            self.lbl_progress.setText("Obteniendo geometrías…")

    def _on_geoms_error(self, msg):
        self._set_generar_busy(False)
        self.iface.messageBar().pushCritical("Q-CensosBo", msg)

    def _on_geoms_ready(self, geoms, df, ctx, clasificacion):
        from ..core.layer_builder import crear_capa_unidades

        self._set_generar_busy(False)
        try:
            capas = crear_capa_unidades(df, geoms, self._nombre_capa(ctx),
                                        self.iface, clasificacion=clasificacion,
                                        municipio=ctx["municipio"])
            total_geom = sum(len(v) for v in geoms.values())
            con_dato = set(df["geo_code"].astype(str))
            sin_dato = sum(1 for lista in geoms.values()
                           for codigo, _, _ in lista if str(codigo) not in con_dato)
            # `capas` incluye el límite municipal de contexto, que no es un área.
            n_datos = len([a for a in ("urbana", "rural") if geoms.get(a)])
            detalle = f"Mapa generado: {total_geom} unidades en {n_datos} capa(s)."
            if sin_dato > 0:
                detalle += f" {sin_dato} sin ficha (sin dato)."
            self.iface.messageBar().pushSuccess("Q-CensosBo", detalle)
        except Exception as exc:
            self.iface.messageBar().pushCritical("Q-CensosBo", f"Error: {exc}")
