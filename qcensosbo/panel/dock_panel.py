"""
Panel lateral del plugin Q-CensosBo.

Controles:
  - Año / Tabla / Nivel / Departamento (+ Municipio y Área en el nivel de
    manzano/comunidad)
  - Variable: "Conteo de registros" más todas las columnas del parquet leídas del
    schema (con el tipo abreviado). En las tablas de fichas viene del catálogo
    empaquetado.
  - Agregación según tipo: conteo → Conteo; categórica → Porcentaje | Moda;
    numérica → Media | Mediana | Suma | Desviación; fichas → Total | % del bloque.
    Si el diccionario no declara el tipo, se ofrecen todas y se avisa.
  - Categoría (solo con "Porcentaje"), con las etiquetas del codebook o, si no
    hay, con los valores distintos que trae el propio parquet.

El estado de todos estos controles lo decide UNA función, `_sync_controls`: antes
había tres rutas que se contradecían (se ocultaba la clasificación en mapas que sí
eran graduados, o se pedía una categoría cuyo selector estaba invisible).

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
    normalize_code, variable_coverage, distinct_values,
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

# Opción de cabecera del selector de variables: da un primer mapa útil (población
# o viviendas empadronadas por unidad) sin tener que elegir nada.
CONTEO_KEY = "__count__"
CONTEO_LABEL = "Conteo de registros (población / viviendas)"

# Valores centinela que ocupan el combo de variables cuando no hay catálogo.
NO_VAR = ("__loading__", "__error__")


def fmt_num(x, decimales=2, pct=False):
    """Formato numérico español: miles con punto, decimales con coma.

    Un único formateador para todo el resumen. Antes convivían tres formatos en
    la misma pantalla (`16,666,665.5`, `16.666.666` y `9,876,543`), mezclando la
    convención inglesa y la española en una UI en español.
    """
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    if pct:
        return f"{v:.1f}".replace(".", ",") + "%"
    if abs(v - round(v)) < 1e-9:
        entero = f"{int(round(v)):,}".replace(",", ".")
        return entero
    txt = f"{v:,.{decimales}f}"
    entero, _, dec = txt.partition(".")
    dec = dec.rstrip("0")
    entero = entero.replace(",", ".")
    return f"{entero},{dec}" if dec else entero

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

GEO_COLS = {"idep", "iprov", "imun", "i00", "area", "area_cod", "ubigeo",
            "dpto", "dep", "departamento", "cod_dep", "depto",
            "mun", "municipio", "cod_mun", "comun"}

# Ayuda de cada campo: qué es, y un ejemplo. Se muestra como tooltip del rótulo y
# del propio control, y es el mismo texto que documenta `docs/uso.md`.
AYUDA = {
    "anio": (
        "Censo de población y vivienda del que salen los datos.\n"
        "Disponibles: 2024, 2012, 2001, 1992 y 1976.\n"
        "Ejemplo: 2024 para el censo más reciente (CPV-2024)."
    ),
    "tabla": (
        "Qué unidad describe cada registro del archivo.\n\n"
        "• Personas — una fila por persona empadronada (sexo, edad, educación…).\n"
        "• Viviendas — una fila por vivienda (materiales, servicios, tenencia).\n"
        "• Emigración — una fila por persona del hogar que se fue del país.\n"
        "• Mortalidad — una fila por fallecimiento declarado en el hogar.\n"
        "• Ficha de indicadores — conteos ya agregados por manzano o comunidad.\n"
        "• Unidades censales — el universo de manzanos y comunidades.\n\n"
        "Las tablas disponibles cambian según el año.\n"
        "Ejemplo: Viviendas + «material de la pared» para un mapa de materiales."
    ),
    "nivel": (
        "Unidad geográfica en la que se agrupa el resultado: un valor por cada\n"
        "una de estas unidades, y un polígono por cada valor en el mapa.\n\n"
        "• Departamental — 9 unidades.\n"
        "• Municipal — 339 unidades.\n"
        "• Manzano/Comunidad — 268.604 unidades (solo fichas del CPV-2024).\n\n"
        "Solo aparecen los niveles que existen para el año y la tabla elegidos."
    ),
    "departamento": (
        "Restringe el cálculo a un departamento en vez de a todo el país.\n"
        "El valor de referencia del resumen pasa a ser el departamental.\n"
        "Ejemplo: Cochabamba (03) para mapear solo sus 47 municipios."
    ),
    "municipio": (
        "Municipio que se va a mapear por manzano y comunidad. Es obligatorio en\n"
        "ese nivel: el país entero son 268.604 unidades y no se mapean de una vez.\n"
        "Ejemplo: Cochabamba (030101), El Alto (020105)."
    ),
    "area": (
        "Qué unidades censales incluir.\n\n"
        "• Urbana — manzanos, que son polígonos.\n"
        "• Rural — comunidades, que el INE publica como puntos.\n"
        "• Ambas — dos capas agrupadas que comparten la escala de color,\n"
        "  para poder comparar lo urbano con lo rural."
    ),
    "variable": (
        "Qué se mide. La primera opción, «Conteo de registros», cuenta personas o\n"
        "viviendas y no necesita configurar nada más.\n\n"
        "El resto son las columnas del archivo, con su descripción del diccionario\n"
        "oficial y el tipo entre paréntesis: (num) numérica, (cat) categórica,\n"
        "(txt) texto. El tipo decide qué agregaciones tienen sentido.\n\n"
        "Ejemplo: p26_edad (num) para la edad; p25_sexo (cat) para el sexo."
    ),
    "agregacion": (
        "Cómo se resume la variable dentro de cada unidad geográfica.\n\n"
        "Numéricas: Media, Mediana, Suma, Desviación estándar.\n"
        "Categóricas:\n"
        "• Porcentaje de una categoría — % de la categoría que elijas, calculado\n"
        "  sobre los casos CON DATO (no sobre todos los registros).\n"
        "• Moda — la categoría más frecuente; da un mapa de colores por categoría.\n"
        "Fichas: Total (conteo) o % del total de su bloque temático.\n\n"
        "Ejemplo: Media de p26_edad = edad promedio de cada municipio."
    ),
    "categoria": (
        "Categoría cuyo porcentaje se va a mapear. Sale del diccionario de\n"
        "etiquetas del censo; si la variable no está en él, se leen los valores\n"
        "del propio archivo.\n"
        "Ejemplo: en p25_sexo, «2 — Hombre» mapea el % de hombres."
    ),
    "clasificacion": (
        "Cómo se agrupan los valores en las clases de color del mapa.\n\n"
        "• Natural Breaks (Jenks) — busca los cortes naturales de los datos.\n"
        "  Es el mejor punto de partida para ver patrones.\n"
        "• Cuantiles — la misma cantidad de unidades en cada clase. Útil para\n"
        "  ordenar, engañoso si los valores están muy concentrados.\n"
        "• Intervalo igual — clases del mismo ancho. Bueno con porcentajes.\n"
        "• Desviación estándar — clases según la distancia a la media.\n\n"
        "El resumen muestra las MISMAS clases que tendrá la leyenda del mapa."
    ),
}


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
    (claves de join *_REF_ID, REDCODEN) que no son variables de análisis.

    `area` entra aquí: es geográfica, ya tiene su propio filtro «Área» en la
    sección de datos, y al no traer tipo en el diccionario heredaba en silencio
    las agregaciones de la variable anterior (media de urbano/rural)."""
    c = col.lower()
    return (c in GEO_COLS
            or c.endswith("_ref_id")
            or c in ("redcoden",))


# ─────────────────────────────────────────────────────────────────────────────
# Workers
#
# Las señales de resultado se llaman `done`/`listo`, no `finished`: redefinir
# `finished` en una subclase de QThread sombrea la señal nativa que Qt emite al
# terminar el hilo.
# ─────────────────────────────────────────────────────────────────────────────

class InstallWorker(QThread):
    status = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def run(self):
        install_duckdb(
            status_cb=lambda m: self.status.emit(m),
            done_cb=lambda ok, msg="": self.done.emit(ok, msg),
        )


class ColumnsWorker(QThread):
    """Lee el schema del parquet y el diccionario del año en segundo plano.

    `token` identifica la petición: si el usuario cambia de año o tabla mientras
    la lectura remota está en vuelo, el panel compara el token y descarta el
    resultado obsoleto. Antes se intentaba abortar con `quit()` + `wait(300)`,
    que sobre un `run()` bloqueante no cancela nada y solo congelaba la UI 300 ms
    en cada cambio de selección.
    """
    # columns, {var: desc}, {var: tipo}, token
    done = pyqtSignal(list, dict, dict, int)

    def __init__(self, path_or_url, anio, tabla=None, remote=False, token=0):
        super().__init__()
        self.path_or_url = path_or_url
        self.anio = anio
        self.tabla = tabla
        self.remote = remote
        self.token = token

    def run(self):
        try:
            cols = get_columns(self.path_or_url, self.remote)
            descs = get_var_descriptions(self.anio, self.tabla)
            types = get_var_types(self.anio, self.tabla)
            self.done.emit(cols, descs, types, self.token)
        except Exception:
            self.done.emit([], {}, {}, self.token)


class CategoriesWorker(QThread):
    """Valores distintos de una variable, para las categóricas sin etiquetas.

    10 de las 104 variables categóricas de 2024 no están en el diccionario de
    etiquetas. Sin este respaldo, «Porcentaje» exigía elegir una categoría que la
    UI no tenía cómo ofrecer.
    """
    done = pyqtSignal(list, int)

    def __init__(self, urls, variable, token=0):
        super().__init__()
        self.urls = urls
        self.variable = variable
        self.token = token

    def run(self):
        try:
            self.done.emit(distinct_values(self.urls, self.variable), self.token)
        except Exception:
            self.done.emit([], self.token)


class MapWorker(QThread):
    progress = pyqtSignal(int)
    status = pyqtSignal(str)
    done = pyqtSignal(object)
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
                self.done.emit({"df": df, "national": national})
                return

            self.status.emit(
                f"Consultando GitHub (sin descarga) por {unidad}…")

            self.progress.emit(60)
            df = agregar_datos(urls, self.nivel, self.variable,
                               self.agg, self.category,
                               departamento=self.departamento,
                               municipio=self.municipio, area=self.area)

            # Valor de referencia (nacional, o del territorio filtrado)
            self.status.emit("Calculando valor de referencia…")
            self.progress.emit(80)
            national = resumen_nacional(urls, self.variable, self.agg,
                                        self.category,
                                        departamento=self.departamento,
                                        municipio=self.municipio,
                                        area=self.area)

            # El porcentaje se calcula sobre los casos válidos, así que hay que
            # poder decir cuál es ese universo (muchas preguntas del censo solo
            # aplican a un subgrupo). Dos COUNT sobre una columna: barato.
            cobertura = (None, None)
            if self.agg == "pct_category":
                self.status.emit("Comprobando la base de cálculo…")
                self.progress.emit(90)
                cobertura = variable_coverage(
                    urls, self.variable, departamento=self.departamento,
                    municipio=self.municipio, area=self.area)
            self.progress.emit(95)
            self.done.emit({"df": df, "national": national,
                            "cobertura": cobertura})
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
    done = pyqtSignal(object)
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
            self.done.emit(geoms)
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
        self._cats_worker = None
        self._var_descriptions = {}
        self._var_types = {}                 # {variable: "categorica"|"numerica"|"texto"}
        # "count" | "categorical" | "numeric" | "ficha" | "unknown" | None
        self._current_var_type = None
        # Token de petición: descarta los resultados de workers obsoletos cuando
        # el usuario cambia de año/tabla/variable mientras hay una lectura remota.
        self._cols_token = 0
        self._cats_token = 0
        # Categorías que el diccionario de etiquetas no cubre y hubo que leer del
        # propio parquet: {(anio, tabla, variable): [valores]}.
        self._cats_fallback = {}
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

    @staticmethod
    def _lbl_ayuda(texto, clave):
        """Rótulo de campo con la ayuda de AYUDA[clave] como tooltip."""
        lbl = QLabel(texto)
        lbl.setToolTip(AYUDA[clave])
        return lbl

    def _compact(self, combo, n_chars=10, ayuda=None):
        """Evita que un combo estire el panel más allá de su ancho útil.

        Sin esto, el contenedor pedía 427 px de ancho mínimo (por ítems como
        «Ficha de indicadores (manzano/comunidad)» o las etiquetas de categoría),
        mientras el panel arranca en 290 px y tiene el scroll horizontal
        desactivado: TODOS los combos quedaban cortados en el borde derecho, sin
        su flecha de despliegue.

        Como consecuencia el texto elegido puede quedar recortado, así que el
        tooltip lleva la ayuda del campo MÁS la selección completa. Se compone en
        `_tooltip_combo` para que actualizar la selección no borre la ayuda.
        """
        combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(n_chars)
        try:
            combo.view().setTextElideMode(Qt.ElideRight)
        except Exception:
            pass
        if ayuda:
            combo.setProperty("ayuda", ayuda)
        combo.currentIndexChanged.connect(
            lambda _i, c=combo: c.setToolTip(self._tooltip_combo(c)))
        combo.setToolTip(self._tooltip_combo(combo))
        return combo

    @staticmethod
    def _tooltip_combo(combo):
        """Ayuda del campo + la selección completa (que en el combo va recortada)."""
        ayuda = combo.property("ayuda") or ""
        texto = combo.currentText()
        if ayuda and texto:
            return f"{ayuda}\n\nSeleccionado: {texto}"
        return ayuda or texto

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

        self.combo_anio = self._compact(QComboBox(), ayuda=AYUDA["anio"])
        self.combo_anio.addItems(["2024", "2012", "2001", "1992", "1976"])
        form_datos.addRow(self._lbl_ayuda("Año:", "anio"), self.combo_anio)

        self.combo_tabla = self._compact(QComboBox(), ayuda=AYUDA["tabla"])
        form_datos.addRow(self._lbl_ayuda("Tabla:", "tabla"), self.combo_tabla)

        # El combo se repuebla según el censo y la tabla (_update_nivel_options):
        # solo lleva los niveles que existen para esa combinación, y el campo
        # entero desaparece cuando la tabla determina el nivel.
        self.lbl_nivel = self._lbl_ayuda("Nivel:", "nivel")
        self.combo_nivel = self._compact(QComboBox(), ayuda=AYUDA["nivel"])
        form_datos.addRow(self.lbl_nivel, self.combo_nivel)

        self.lbl_nivel_fijo = QLabel("")
        self.lbl_nivel_fijo.setObjectName("lbl_var_desc")
        self.lbl_nivel_fijo.setWordWrap(True)
        self.lbl_nivel_fijo.setVisible(False)
        form_datos.addRow(self.lbl_nivel_fijo)

        self.lbl_depto = self._lbl_ayuda("Departamento:", "departamento")
        self.combo_depto = self._compact(QComboBox(), ayuda=AYUDA["departamento"])
        for lbl, code in DEPTOS:
            self.combo_depto.addItem(lbl, code)
        self.lbl_depto.setVisible(False)
        self.combo_depto.setVisible(False)
        form_datos.addRow(self.lbl_depto, self.combo_depto)

        # Municipio y área solo aplican al nivel de manzano/comunidad: el país
        # entero son 268.604 unidades, así que se mapea de a un municipio.
        self.lbl_municipio = self._lbl_ayuda("Municipio:", "municipio")
        self.combo_municipio = self._compact(QComboBox(), ayuda=AYUDA["municipio"])
        self.lbl_municipio.setVisible(False)
        self.combo_municipio.setVisible(False)
        form_datos.addRow(self.lbl_municipio, self.combo_municipio)

        self.lbl_area = self._lbl_ayuda("Área:", "area")
        self.combo_area = self._compact(QComboBox(), ayuda=AYUDA["area"])
        for lbl, key in AREAS_OPCIONES:
            self.combo_area.addItem(lbl, key)
        self.lbl_area.setVisible(False)
        self.combo_area.setVisible(False)
        form_datos.addRow(self.lbl_area, self.combo_area)

        main.addWidget(grp_datos)

        # ── Sección: Análisis ─────────────────────────────────────────────────
        grp_analisis = QGroupBox("Análisis")
        form_analisis = QFormLayout(grp_analisis)
        form_analisis.setSpacing(6)
        form_analisis.setContentsMargins(8, 14, 8, 8)

        # Labels guardados explícitamente: _sync_controls los oculta junto a su
        # campo (antes se buscaban recorriendo el QFormLayout, y la descripción
        # de la variable se quedaba visible en el modo SQL).
        self.lbl_variable = self._lbl_ayuda("Variable:", "variable")
        self.combo_variable = self._compact(QComboBox(), ayuda=AYUDA["variable"])
        form_analisis.addRow(self.lbl_variable, self.combo_variable)

        self.lbl_var_desc = QLabel("")
        self.lbl_var_desc.setObjectName("lbl_var_desc")
        self.lbl_var_desc.setWordWrap(True)
        self.lbl_var_desc.setVisible(False)
        form_analisis.addRow(self.lbl_var_desc)

        # Las opciones las define _update_agg_options según el tipo de variable.
        self.lbl_agg = self._lbl_ayuda("Agregación:", "agregacion")
        self.combo_agg = self._compact(QComboBox(), ayuda=AYUDA["agregacion"])
        form_analisis.addRow(self.lbl_agg, self.combo_agg)

        self.lbl_agg_aviso = QLabel("")
        self.lbl_agg_aviso.setObjectName("lbl_aviso")
        self.lbl_agg_aviso.setWordWrap(True)
        self.lbl_agg_aviso.setVisible(False)
        form_analisis.addRow(self.lbl_agg_aviso)

        CLASIFICACION_OPTIONS = [
            ("Natural Breaks (Jenks)", "jenks"),
            ("Cuantiles",              "quantile"),
            ("Intervalo igual",        "equal"),
            ("Desviación estándar",    "stddev"),
        ]
        self.combo_clasificacion = self._compact(QComboBox(), ayuda=AYUDA["clasificacion"])
        for lbl, key in CLASIFICACION_OPTIONS:
            self.combo_clasificacion.addItem(lbl, key)
        self._lbl_clasificacion = self._lbl_ayuda("Clasificación:", "clasificacion")
        form_analisis.addRow(self._lbl_clasificacion, self.combo_clasificacion)

        self.lbl_categoria = self._lbl_ayuda("Categoría:", "categoria")
        self.combo_categoria = self._compact(QComboBox(), ayuda=AYUDA["categoria"])
        self.lbl_categoria.setVisible(False)
        self.combo_categoria.setVisible(False)
        form_analisis.addRow(self.lbl_categoria, self.combo_categoria)

        # Los dos pasos tienen el MISMO peso visual (objectName `btn_paso`); el
        # énfasis lo mueve `_update_button_emphasis` con una propiedad dinámica,
        # del paso 1 al paso 2 en cuanto hay resultado. Antes "Generar mapa" era
        # el único botón sólido, así que atraía el clic estando apagado.
        self.btn_consultar = QPushButton("1 · Consultar")
        self.btn_consultar.setObjectName("btn_paso")
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
        self.btn_generar.setObjectName("btn_paso")
        self.btn_generar.setCursor(Qt.PointingHandCursor)
        self.btn_generar.setEnabled(False)   # se habilita tras "Consultar"
        self.btn_generar.setToolTip(
            "Dibuja el mapa con el resultado ya consultado.")
        action_l.addWidget(self.btn_generar)
        self._update_button_emphasis()

        main.addWidget(action_w)
        main.addStretch()

    def _update_button_emphasis(self):
        """Marca cuál de los dos pasos es el siguiente que hay que pulsar.

        `paso_activo` es una propiedad dinámica que el QSS usa para decidir el
        relleno: mientras no hay resultado el énfasis está en «1 · Consultar»; en
        cuanto lo hay, pasa a «2 · Generar mapa».
        """
        activo_2 = bool(self._agg_result)
        for widget, activo in ((self.btn_consultar, not activo_2),
                               (self.btn_generar, activo_2)):
            if widget.property("paso_activo") == activo:
                continue
            widget.setProperty("paso_activo", activo)
            # Qt no reevalúa el QSS al cambiar una propiedad: hay que forzarlo.
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()

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
        self._install_worker.done.connect(self._on_install_done)
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
        # No se habilita "Generar mapa": solo lo habilita un Consultar con
        # resultado. Antes se activaba aquí (incluso al FALLAR la instalación),
        # ofreciendo un botón que solo podía responder "pulsa Consultar primero".
        self._invalidate_result()
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
        self._update_nivel_options()
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

    def _niveles_aplicables(self):
        """Niveles que existen para el censo y la tabla elegidos, en orden.

        - Las tablas de fichas del CPV-2024 SON datos de manzano y comunidad: ese
          es su único nivel en el panel.
        - En los microdatos la unidad es la persona o la vivienda, así que el
          nivel de manzano/comunidad no existe.
        - 1976 no tiene nivel municipal (su unidad menor es el cantón).
        """
        if self._es_ficha():
            return ["unidad"]
        anio = int(self.combo_anio.currentText())
        return ["departamento"] if anio == 1976 else ["departamento", "municipio"]

    def _update_nivel_options(self):
        """Repuebla el combo de nivel con lo aplicable, y lo oculta si es único.

        Antes los niveles no aplicables aparecían tachados: un ítem deshabilitado
        ocupa sitio y hace pensar que falta algo por desbloquear. Ahora simplemente
        no están, y si solo queda uno el campo desaparece y una línea explica cuál
        es el nivel de esos datos.
        """
        aplicables = self._niveles_aplicables()
        etiquetas = dict((k, l) for l, k in NIVELES)
        prev = self.combo_nivel.currentData()

        self.combo_nivel.blockSignals(True)
        self.combo_nivel.clear()
        for lbl, key in NIVELES:
            if key in aplicables:
                self.combo_nivel.addItem(lbl, key)
        idx = self.combo_nivel.findData(prev) if prev else -1
        self.combo_nivel.setCurrentIndex(idx if idx >= 0 else 0)
        self.combo_nivel.blockSignals(False)

        unico = len(aplicables) == 1
        self.lbl_nivel.setVisible(not unico)
        self.combo_nivel.setVisible(not unico)
        self.lbl_nivel_fijo.setVisible(unico)
        if unico:
            nivel = aplicables[0]
            if nivel == "unidad":
                self.lbl_nivel_fijo.setText(
                    "Nivel: manzano urbano y comunidad rural "
                    "(es la unidad de estos datos).")
            else:
                self.lbl_nivel_fijo.setText(
                    f"Nivel: {etiquetas.get(nivel, nivel).lower()} "
                    "(el único disponible para este censo).")

        # La ayuda del campo explica además lo que ya no se ve en la lista.
        avisos = []
        if "municipio" not in aplicables and not self._es_ficha():
            avisos.append("Aquí no está el nivel municipal: el censo 1976 usa "
                          "cantón, no municipio.")
        if "unidad" not in aplicables:
            avisos.append("Aquí no está manzano/comunidad: solo existe en las "
                          "tablas de fichas del CPV-2024.")
        ayuda = AYUDA["nivel"] + ("\n\n" + "\n".join(avisos) if avisos else "")
        self.combo_nivel.setProperty("ayuda", ayuda)
        self.combo_nivel.setToolTip(self._tooltip_combo(self.combo_nivel))
        self.lbl_nivel.setToolTip(ayuda)
        self.lbl_nivel_fijo.setToolTip(ayuda)

    # ── Máquina de estado única de los controles ──────────────────────────────

    def _mapa_es_graduado(self):
        """True si el mapa que se va a dibujar usa una rampa de color continua.

        Solo la Moda produce un mapa categórico. Todo lo demás —porcentaje,
        agregaciones numéricas, indicadores de ficha y expresiones SQL— es
        graduado, y por tanto el método de clasificación SÍ aplica.
        """
        if self.chk_avanzado.isChecked():
            return True
        return self.combo_agg.currentData() != "mode"

    def _sync_controls(self):
        """Fija el estado de TODOS los controles de análisis de una vez.

        Única fuente de verdad de la visibilidad y habilitación. Sustituye a las
        tres rutas que antes se contradecían entre sí y dejaban estados
        imposibles: la clasificación oculta en mapas graduados, la categoría
        exigida con su selector invisible, o la descripción de una variable oculta
        siguiendo a la vista en el modo SQL.
        """
        sql = self.chk_avanzado.isChecked()
        tipo = self._current_var_type
        agg = self.combo_agg.currentData()

        # Variable / agregación: las reemplaza la expresión en el modo SQL.
        for w in (self.lbl_variable, self.combo_variable,
                  self.lbl_agg, self.combo_agg):
            w.setVisible(not sql)
        self.txt_sql.setVisible(sql)
        self.lbl_sql_hint.setVisible(sql)

        # Descripción de la variable: solo si hay una variable a la vista.
        var = self.combo_variable.currentData() or ""
        desc = self._var_descriptions.get(var)
        hay_desc = bool(desc) and var not in NO_VAR and var != CONTEO_KEY
        self.lbl_var_desc.setVisible(not sql and hay_desc)
        if hay_desc:
            self.lbl_var_desc.setText(desc)

        # Aviso de tipo desconocido: es la única vía por la que el usuario puede
        # elegir una agregación sin sentido para la variable, así que se dice.
        aviso = ""
        if not sql and tipo == "unknown":
            aviso = ("El diccionario no declara el tipo de esta variable: "
                     "elige la agregación con criterio.")
        elif not sql and tipo in ("categorical", "unknown"):
            estado = self._estado_categorias()
            if estado == "vacio":
                aviso = ("Sin catálogo de categorías para esta variable, "
                         "así que «Porcentaje» no está disponible.")
            elif estado == "cargando":
                aviso = "Leyendo las categorías de esta variable…"
        self.lbl_agg_aviso.setText(aviso)
        self.lbl_agg_aviso.setVisible(bool(aviso))

        # Clasificación: solo cuando el mapa es graduado (incluye porcentaje y SQL).
        graduado = self._mapa_es_graduado()
        self.combo_clasificacion.setVisible(graduado)
        self._lbl_clasificacion.setVisible(graduado)

        # Categoría: solo con "Porcentaje" sobre una categórica, y con opciones.
        mostrar_cat = (not sql and tipo == "categorical"
                       and agg == "pct_category")
        self.lbl_categoria.setVisible(mostrar_cat)
        self.combo_categoria.setVisible(mostrar_cat)
        if mostrar_cat and self.combo_categoria.count() == 0:
            self.combo_categoria.addItem("(Cargando categorías…)", None)

    def _estado_categorias(self):
        """'ok' | 'cargando' | 'vacio' para la variable actual.

        Los tres estados hay que distinguirlos: mientras la lectura del parquet
        está en vuelo no se sabe todavía si hay categorías, y tratarlo como
        'vacio' hacía que el panel afirmara «sin catálogo de categorías» —y
        deshabilitara «Porcentaje»— durante un par de segundos, para luego
        desdecirse.
        """
        var = self.combo_variable.currentData() or ""
        if not var or var in NO_VAR or var == CONTEO_KEY:
            return "ok"
        anio = int(self.combo_anio.currentText())
        tabla = self.combo_tabla.currentData() or ""
        if get_value_labels(anio, var, tabla):
            return "ok"
        valores = self._cats_fallback.get((anio, tabla, var))
        if valores is None:
            return "cargando"
        return "ok" if valores else "vacio"

    def _sin_categorias(self):
        """True solo cuando se sabe con certeza que no hay ninguna categoría."""
        return self._estado_categorias() == "vacio"

    def _on_agg_changed(self):
        if (self._current_var_type == "categorical"
                and self.combo_agg.currentData() == "pct_category"):
            self._populate_categorias(self.combo_variable.currentData() or "")
        self._sync_controls()
        self._invalidate_result()

    def _invalidate_result(self):
        """El resultado de Consultar dejó de ser válido: hay que volver a consultar."""
        self._agg_result = None
        self.btn_generar.setEnabled(False)
        self._update_button_emphasis()

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
            if not variable or variable in NO_VAR:
                self.iface.messageBar().pushWarning("Q-CensosBo", "Selecciona una variable.")
                return
            if agg == "pct_category":
                category = self.combo_categoria.currentData()
                if not category:
                    self.iface.messageBar().pushWarning(
                        "Q-CensosBo",
                        "Aún se están cargando las categorías de esta variable. "
                        "Espera un momento y vuelve a consultar."
                        if self.combo_categoria.count()
                        else "Esta variable no tiene categorías: usa «Moda» o "
                             "elige otra variable.")
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
        self._map_worker.done.connect(
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
        # Hay resultado: el siguiente paso es dibujar el mapa.
        self._update_button_emphasis()

    def _on_consulta_error(self, msg):
        self._set_consulta_busy(False)
        self._invalidate_result()
        self.iface.messageBar().pushCritical("Q-CensosBo", msg)
        self._show_stats_hint("Error en la consulta. Revisa los parámetros.")

    @staticmethod
    def _cat_sort_key(code):
        c = normalize_code(code)
        return (0, int(c)) if c.lstrip("-").isdigit() else (1, str(code))

    def _populate_categorias(self, variable):
        """Llena el selector de categoría con las etiquetas del diccionario.

        Si el diccionario no cubre la variable (10 de las 104 categóricas de 2024
        no están), se piden los valores distintos al propio parquet en segundo
        plano. Antes ese caso dejaba el selector vacío y oculto mientras
        «Porcentaje» seguía exigiendo una categoría: sin salida.
        """
        if not variable or variable in NO_VAR or variable == CONTEO_KEY:
            return
        anio = int(self.combo_anio.currentText())
        tabla = self.combo_tabla.currentData() or ""
        labels = get_value_labels(anio, variable, tabla)

        prev = self.combo_categoria.currentData()
        self.combo_categoria.blockSignals(True)
        self.combo_categoria.clear()
        if labels:
            for code in sorted(labels.keys(), key=self._cat_sort_key):
                self.combo_categoria.addItem(f"{code} — {labels[code]}", code)
        else:
            valores = self._cats_fallback.get((anio, tabla, variable))
            if valores is None:
                self.combo_categoria.addItem("(Cargando categorías…)", None)
                self._start_cats_worker(anio, tabla, variable)
            elif valores:
                for v in sorted(valores, key=self._cat_sort_key):
                    self.combo_categoria.addItem(str(v), v)
        idx = self.combo_categoria.findData(prev) if prev else -1
        self.combo_categoria.setCurrentIndex(idx if idx >= 0 else 0)
        self.combo_categoria.blockSignals(False)

    def _start_cats_worker(self, anio, tabla, variable):
        """Pide al parquet los valores distintos de una variable sin catálogo."""
        if not duckdb_available():
            return
        try:
            urls = get_parquet_urls(anio, tabla)
        except Exception:
            return
        self._cats_token += 1
        token = self._cats_token
        w = CategoriesWorker(urls, variable, token=token)
        w.done.connect(
            lambda vals, tk: self._on_cats_loaded(vals, tk, anio, tabla, variable))
        self._cats_worker = w
        w.start()

    def _on_cats_loaded(self, valores, token, anio, tabla, variable):
        if token != self._cats_token:
            return                      # respuesta de una selección ya abandonada
        self._cats_fallback[(anio, tabla, variable)] = list(valores)
        if (int(self.combo_anio.currentText()) == anio
                and (self.combo_tabla.currentData() or "") == tabla
                and (self.combo_variable.currentData() or "") == variable):
            self._populate_categorias(variable)
            self._update_agg_options()
            self._sync_controls()

    def _on_avanzado_toggled(self, checked):
        self._sync_controls()
        self._invalidate_result()

    def _var_type_actual(self):
        """Tipo interno de la variable elegida.

        'count' para la opción de conteo, 'ficha' en las tablas de manzano y
        comunidad, y el tipo del diccionario en el resto. 'unknown' cuando el
        diccionario no lo declara: antes ese caso dejaba intacto el combo de
        agregación, que seguía ofreciendo —y ejecutando— las opciones de la
        variable anterior.
        """
        var = self.combo_variable.currentData() or ""
        if var in NO_VAR:
            return None
        if var == CONTEO_KEY:
            return "count"
        if self._es_ficha():
            return "ficha"
        return TIPO_MAP.get((self._var_types.get(var) or "").lower()) or "unknown"

    def _on_variable_changed(self):
        self._invalidate_result()

        # Limpiar categorías de la variable anterior
        self.combo_categoria.blockSignals(True)
        self.combo_categoria.clear()
        self.combo_categoria.blockSignals(False)

        self._current_var_type = self._var_type_actual()
        self._update_agg_options()
        if self._current_var_type == "categorical":
            self._populate_categorias(self.combo_variable.currentData() or "")
        self._sync_controls()

        self._show_stats_hint(
            "Pulsa '1 · Consultar' para calcular\nla agregación y ver el resumen.")

    def _update_agg_options(self):
        """Repuebla el combo de agregación según el tipo de la variable actual.

        Se llama siempre que cambia la variable (nunca se conserva el contenido
        anterior). «Porcentaje» se deshabilita —no se oculta— si la variable no
        tiene ninguna categoría que elegir, para que el motivo sea visible.
        """
        var_type = self._current_var_type
        current = self.combo_agg.currentData()

        if var_type == "count":
            options = [("Conteo de registros", CONTEO_KEY)]
        elif var_type == "ficha":
            # Los indicadores de ficha ya son conteos: se suman, o se dividen
            # entre el total de su bloque para obtener la proporción.
            options = [("Total (conteo)", "total")]
            if fichas.tiene_porcentaje(self.combo_tabla.currentData(),
                                       self.combo_variable.currentData() or ""):
                options.append(("% del total del bloque", "porcentaje"))
        elif var_type == "categorical":
            options = [
                ("Porcentaje de una categoría", "pct_category"),
                ("Moda (categoría más frecuente)", "mode"),
            ]
        elif var_type == "numeric":
            options = [
                ("Media",               "mean"),
                ("Mediana",             "median"),
                ("Suma",                "sum"),
                ("Desviación estándar", "std"),
            ]
        elif var_type == "unknown":
            # Sin tipo declarado: se ofrecen todas y `_sync_controls` avisa.
            options = [
                ("Porcentaje de una categoría", "pct_category"),
                ("Moda (categoría más frecuente)", "mode"),
                ("Media",               "mean"),
                ("Mediana",             "median"),
                ("Suma",                "sum"),
                ("Desviación estándar", "std"),
            ]
        else:
            options = []

        self.combo_agg.blockSignals(True)
        self.combo_agg.clear()
        for lbl, key in options:
            self.combo_agg.addItem(lbl, key)

        # Una categórica sin catálogo no puede dar un "% de X": se deshabilita la
        # opción y se elige Moda, en vez de dejar que el usuario choque con un
        # aviso pidiéndole una categoría inexistente.
        deshabilitar_pct = (var_type in ("categorical", "unknown")
                            and self._sin_categorias())
        if deshabilitar_pct:
            i = self.combo_agg.findData("pct_category")
            if i >= 0:
                item = self.combo_agg.model().item(i)
                item.setEnabled(False)
                self.combo_agg.setItemData(
                    i, "Esta variable no tiene catálogo de categorías.",
                    Qt.ToolTipRole)

        idx = self.combo_agg.findData(current)
        if idx < 0 or (deshabilitar_pct and current == "pct_category"):
            idx = 0
        if deshabilitar_pct and self.combo_agg.itemData(idx) == "pct_category":
            idx = 1 if self.combo_agg.count() > 1 else 0
        self.combo_agg.setCurrentIndex(idx)
        self.combo_agg.blockSignals(False)

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
            # El conteo de registros no necesita el schema: se ofrece ya, así el
            # panel es utilizable mientras las variables cargan.
            self.combo_variable.addItem(CONTEO_LABEL, CONTEO_KEY)
            self.combo_variable.addItem("(Cargando variables…)", "__loading__")
        else:
            # El motor aún se está instalando/preparando: no hay worker que lanzar.
            # _on_install_done vuelve a llamar a este método cuando esté listo.
            self.combo_variable.addItem(
                "(Esperando al motor de consulta…)", "__loading__")
        self.combo_variable.blockSignals(False)
        self._on_variable_changed()

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
        """Lanza la lectura del schema. El worker en vuelo no se aborta (no se
        puede interrumpir un `run()` bloqueante): se le asigna un token y su
        resultado se descarta si ya no corresponde a la selección actual."""
        anio = int(self.combo_anio.currentText())
        tabla = self.combo_tabla.currentData() or ""
        self._cols_token += 1
        w = ColumnsWorker(path_or_url, anio=anio, tabla=tabla, remote=remote,
                          token=self._cols_token)
        w.done.connect(
            lambda cols, descs, types, tk: self._on_columns_loaded(
                cols, descs, types, tk, anio, tabla)
        )
        self._cols_worker = w
        w.start()

    def _on_columns_loaded(self, columns, descriptions, types, token, anio, tabla):
        if token != self._cols_token:
            return                      # lectura de una selección ya abandonada
        if int(self.combo_anio.currentText()) != anio:
            return
        if self.combo_tabla.currentData() != tabla:
            return

        self._var_descriptions = descriptions
        self._var_types = types

        # Columnas vacías = el worker falló (un parquet real siempre tiene
        # columnas): casi siempre la lectura remota del schema no llegó a GitHub
        # (sin internet, proxy/firewall, o timeout). Mostramos el error en el
        # propio combo en vez de dejarlo en "Cargando…" o vacío y mudo. El conteo
        # de registros se conserva: no depende del diccionario y sigue sirviendo.
        current = self.combo_variable.currentData()
        self.combo_variable.blockSignals(True)
        self.combo_variable.clear()
        self.combo_variable.addItem(CONTEO_LABEL, CONTEO_KEY)
        if not columns:
            self.combo_variable.addItem(
                "⚠ No se pudieron cargar las variables — revisa tu conexión",
                "__error__")
        for col in columns:
            if not _is_geo_or_technical(col):
                desc = descriptions.get(col)
                abbr = TIPO_ABBR.get((types.get(col) or "").lower())
                name = f"{col} ({abbr})" if abbr else col
                label = f"{name} — {desc}" if desc else name
                self.combo_variable.addItem(label, col)

        idx = self.combo_variable.findData(current)
        self.combo_variable.setCurrentIndex(max(idx, 0))
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
        """Resumen del RESULTADO: qué se mapea, valor de referencia, la base de
        cálculo cuando importa, y la distribución entre unidades."""
        import pandas as pd
        df = result.get("df") if isinstance(result, dict) else result
        national = result.get("national") if isinstance(result, dict) else None
        cobertura = (result.get("cobertura") if isinstance(result, dict)
                     else None) or (None, None)

        self._clear_stat_bars()
        self.lbl_stats_hint.setVisible(False)
        self.stats_bars_widget.setVisible(True)

        nivel = ctx["nivel"]
        agg = ctx["agg"]
        sql_libre = ctx.get("sql_libre")
        unidad_sg = UNIDAD_SG.get(nivel, "municipio")
        unidad_pl = UNIDAD_PL.get(nivel, "municipios")

        # Cuántas de estas unidades podrá pintar el mapa. El GeoJSON empaquetado
        # no trae 4 municipios de creación reciente, y antes el resumen decía 343
        # mientras el mapa dibujaba 339, sin avisar.
        n_map, sin_geom = self._cobertura_del_mapa(df, ctx)
        self.lbl_total_caption.setText(f"Unidades ({unidad_pl}):")
        self.lbl_total.setText(str(len(df)))

        # Qué se está mapeando: la línea más importante del resumen. El tooltip
        # lleva la etiqueta completa cuando el título va recortado.
        self._add_title(self._indicator_title(ctx),
                        tooltip=self._var_descriptions.get(ctx.get("variable")))

        # En el modo SQL el título no puede decir qué se calculó, así que se
        # repite la expresión: es lo único que identifica el resultado.
        if sql_libre and ctx.get("sql_expr"):
            expr = " ".join(ctx["sql_expr"].split())
            self._add_note(expr if len(expr) <= 120 else expr[:120] + "…")

        # Valor de referencia, acotado al territorio filtrado.
        natlbl = self._national_label(ctx, national)
        if natlbl is not None:
            if ctx.get("municipio"):
                ref_caption = "Municipal"
            elif ctx.get("depto"):
                ref_caption = "Departamental"
            else:
                ref_caption = "Nacional"
            self._add_kv_row(ref_caption, natlbl)

        # Base de cálculo del porcentaje: es el dato que evita malinterpretar un
        # indicador cuya pregunta solo aplica a un subgrupo del censo.
        n_total, n_validos = cobertura
        if agg == "pct_category" and n_total and n_validos is not None:
            pctv = 100.0 * n_validos / n_total if n_total else 0.0
            texto = (f"Calculado sobre {fmt_num(n_validos)} casos con dato "
                     f"({fmt_num(pctv, pct=True)} de {fmt_num(n_total)} registros).")
            if pctv < 95:
                texto += (" El resto no respondió o la pregunta no le aplica, "
                          "y queda fuera del denominador.")
            self._add_note(texto)

        if ctx["tabla"] in fichas.TABLAS:
            self._add_note(self._aviso_fichas(ctx))

        if sin_geom:
            self._add_note(
                f"⚠ {len(sin_geom)} sin geometría en el mapa "
                f"({', '.join(sin_geom[:4])}{'…' if len(sin_geom) > 4 else ''}): "
                f"se mapearán {n_map} de {len(df)}."
            )

        pct = agg in ("pct_category", "porcentaje") and not sql_libre

        def fmt(x):
            return fmt_num(x, pct=pct)

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
                from ..core.layer_builder import class_bounds, bins_from_bounds
                metodo = self.combo_clasificacion.currentData() or "jenks"
                self._add_section(
                    f"Clases del mapa · {self.combo_clasificacion.currentText()}")
                bounds = class_bounds(vals.tolist(), metodo, 5)
                edges = bins_from_bounds(bounds)
                if not edges:
                    self._add_note("Todos los valores son iguales.")
                    return
                hist, _ = np.histogram(vals.values, bins=edges)
                # La primera clase incluye su borde inferior en la leyenda de QGIS.
                mxc = int(hist.max()) or 1
                for i, c in enumerate(hist):
                    rng = f"{fmt(edges[i])} – {fmt(edges[i + 1])}"
                    self._add_stat_bar(
                        rng, int(c / mxc * 100), text=str(int(c)))
                self._add_note(
                    f"Número de {unidad_pl} en cada clase de color del mapa.")
            else:
                self._add_section(f"Por {unidad_sg} (mayor → menor)")
                tmp = df.assign(_v=pd.to_numeric(
                    df["valor"], errors="coerce")).dropna(subset=["_v"])
                shown = tmp.sort_values("_v", ascending=False).head(15)
                # Barras proporcionales desde CERO cuando todos los valores son
                # positivos: escalarlas al mínimo hacía que una diferencia real
                # del 21 % (edad media 26,4 → 31,9) se viera como 25 veces más.
                vmin, vmax = float(vals.min()), float(vals.max())
                desde_cero = vmin >= 0
                base = 0.0 if desde_cero else vmin
                rango = (vmax - base) or 1.0
                for _, r in shown.iterrows():
                    name = str(r.get("geo_nombre", r["geo_code"]))
                    fill = max(1, min(100, int((r["_v"] - base) / rango * 100)))
                    # El valor va DENTRO de la barra: en la etiqueta se recortaba
                    # justo por donde importa ("Santa Cruz · 3.12…").
                    self._add_stat_bar(name, fill, text=fmt(r["_v"]),
                                       tooltip=f"{name}: {fmt(r['_v'])}")
                if not desde_cero:
                    self._add_note(
                        "Hay valores negativos: las barras son relativas al mínimo "
                        f"({fmt(vmin)}), no a cero.")
                if len(tmp) > len(shown):
                    self._add_note(f"(mostrando {len(shown)} de {len(tmp)})")

    def _cobertura_del_mapa(self, df, ctx):
        """(n_mapeables, codigos_sin_geometria) del resultado actual."""
        if ctx["nivel"] == "unidad":
            return len(df), []          # la geometría llega del release de fichas
        try:
            from ..core.layer_builder import cobertura_geo
            return cobertura_geo(df["geo_code"].astype(str),
                                 ctx["nivel"], ctx.get("depto"))
        except Exception:
            return len(df), []

    def _aviso_fichas(self, ctx):
        """Advertencia sobre la cobertura de las fichas, según lo que se calcule.

        Con «Total» el resultado es una suma incompleta y —esto es lo que importa—
        el hueco NO es uniforme: la cobertura poblacional va del 85 % (Oruro) al
        94 % (La Paz), así que un mapa de totales queda deformado, no solo bajo.
        Con «% del bloque» es una razón entre dos sumas del mismo conjunto de
        unidades, mucho menos sensible.
        """
        base = ("El INE reserva la ficha de las unidades con poca población: las "
                "que no la tienen salen sin dato en el mapa.")
        if ctx["agg"] == "total" and ctx["nivel"] != "unidad":
            return (base + " Ojo: este TOTAL suma solo las unidades con ficha, así "
                    "que subestima el valor real, y de forma desigual entre "
                    "territorios (cubre del 85 % al 94 % de la población según el "
                    "departamento). Para comparar unidades, usa «% del total del "
                    "bloque».")
        return base + " A nivel nacional cubren el 92 % de la población."

    # ── Helpers del resumen ───────────────────────────────────────────────────

    def _var_label(self, var, max_chars=52):
        """Nombre legible de una variable, con el nombre técnico entre paréntesis.

        El combo ya muestra la descripción del diccionario, así que el resumen
        también debe hacerlo: antes decía «Media de p26_edad», obligando a
        traducir mentalmente el código. Se recorta porque algunas etiquetas son
        la pregunta completa del cuestionario («El centro o establecimiento
        educativo al que asiste es:») y el título ocupaba cuatro líneas.
        """
        if not var or var in NO_VAR:
            return str(var)
        desc = (self._var_descriptions.get(var) or "").strip()
        if not desc:
            return var
        # Los diccionarios traen la etiqueta con el número de pregunta ("26. …")
        # y a veces terminada en ":" o ".".
        if desc[:1].isdigit() and ". " in desc:
            desc = desc.split(". ", 1)[1]
        desc = desc.strip().rstrip(":.").strip()
        if len(desc) > max_chars:
            desc = desc[:max_chars].rstrip(" ,;") + "…"
        return f"{desc} ({var})"

    def _indicator_title(self, ctx):
        nivel = ctx["nivel"]
        unidad = UNIDAD_SG.get(nivel, "municipio")
        if ctx.get("sql_libre"):
            return f"Expresión SQL — por {unidad}"
        var = ctx["variable"]
        agg = ctx["agg"]
        if agg == "__count__":
            return f"Conteo de registros — por {unidad}"
        if ctx["tabla"] in fichas.TABLAS:
            etiqueta = self._var_descriptions.get(var, var)
            prefijo = "% de" if agg == "porcentaje" else "Total de"
            return f"{prefijo} {etiqueta} — por {unidad}"
        legible = self._var_label(var)
        templ = {
            "mean":   f"Media de {legible}",
            "median": f"Mediana de {legible}",
            "sum":    f"Suma de {legible}",
            "std":    f"Desv. estándar de {legible}",
            "mode":   f"Categoría más frecuente de {legible}",
        }
        if agg == "pct_category":
            cat = self.combo_categoria.currentText() or str(ctx["category"])
            metric = f"% con «{cat}» en {legible}"
        else:
            metric = templ.get(agg, legible)
        return f"{metric} — por {unidad}"

    def _national_label(self, ctx, national):
        """Valor de referencia ya formateado. Un solo formato para todo el panel.

        En el modo SQL avanzado NUNCA se añade el signo de porcentaje: el sufijo
        lo decidía el combo de agregación oculto, así que una expresión como
        `AVG(p26_edad)` se mostraba como «30,5 %».
        """
        if national is None:
            return None
        agg = ctx["agg"]
        sql_libre = ctx.get("sql_libre")
        try:
            if agg == "mode" and not sql_libre:
                labels = get_value_labels(
                    ctx["anio"], ctx["variable"], ctx["tabla"])
                norm = {normalize_code(k): v for k, v in labels.items()}
                code = str(national)
                lbl = norm.get(normalize_code(code))
                return f"{code} — {lbl}" if lbl else code
            es_pct = agg in ("pct_category", "porcentaje") and not sql_libre
            return fmt_num(national, pct=es_pct)
        except Exception:
            return str(national)

    @staticmethod
    def _elide(text, widget, width):
        """Recorta el texto con "…" al ancho dado, usando la métrica de la fuente."""
        try:
            from qgis.PyQt.QtGui import QFontMetrics
            return QFontMetrics(widget.font()).elidedText(
                text, Qt.ElideRight, max(20, width - 2))
        except Exception:
            return text

    def _add_title(self, text, tooltip=None):
        lbl = QLabel(text)
        lbl.setObjectName("lbl_result_title")
        lbl.setWordWrap(True)
        if tooltip:
            lbl.setToolTip(tooltip)
        self.stats_bars_layout.addWidget(lbl)

    def _add_section(self, text):
        lbl = QLabel(text)
        lbl.setObjectName("lbl_section")
        lbl.setWordWrap(True)
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
        row_l.setSpacing(6)
        # Sin ancho fijo: con 70 px, "Departamental:" se cortaba en
        # "Departament" y el resumen leía «Departament 30.49».
        lk = QLabel(caption + ":")
        lv = QLabel(str(value))
        lv.setObjectName("lbl_stat_value")
        row_l.addWidget(lk)
        row_l.addWidget(lv)
        row_l.addStretch()
        self.stats_bars_layout.addWidget(row_w)

    def _add_stat_bar(self, label, fill, text="", tooltip=None):
        row_w = QWidget()
        row_l = QHBoxLayout(row_w)
        row_l.setContentsMargins(0, 0, 0, 0)
        row_l.setSpacing(4)
        # Elipsis en vez de salto de línea: con wordWrap, un nombre algo largo
        # ("Cochabamba · 30,76") partía la fila en dos y las filas del ranking
        # quedaban de alturas distintas.
        lbl = QLabel()
        lbl.setFixedWidth(134)
        lbl.setText(self._elide(str(label), lbl, 134))
        lbl.setToolTip(str(tooltip if tooltip is not None else label))
        bar = QProgressBar()
        # La barra cede espacio a la etiqueta: en el histograma la etiqueta es el
        # rango de la clase ("39,4 % – 58,3 %"), que recortado no dice nada.
        bar.setMinimumWidth(64)
        bar.setObjectName("stat_bar")
        bar.setRange(0, 100)
        bar.setValue(max(0, min(100, int(fill))))
        # setFormat sustituye los marcadores %p/%v/%m; un "%" sin letra detrás,
        # como el de "49,4%", se muestra literal. Solo hay que neutralizar los
        # marcadores, no duplicar el signo (eso imprimía "49,4%%").
        formato = str(text)
        for marca in ("%p", "%v", "%m"):
            formato = formato.replace(marca, marca[1])
        bar.setFormat(formato)
        bar.setTextVisible(bool(text))
        bar.setFixedHeight(16)
        bar.setToolTip(str(tooltip if tooltip is not None else label))
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

            capa = crear_capa(df, nivel, self._nombre_capa(ctx), self.iface,
                              departamento=departamento,
                              is_categorical=is_categorical,
                              clasificacion=clasificacion,
                              value_labels=value_labels)
            # El conteo sale de la capa, no del DataFrame: son distintos cuando
            # algún código de los datos no tiene polígono en el GeoJSON.
            n_map, sin_geom = self._cobertura_del_mapa(df, ctx)
            detalle = f"Mapa generado: {capa.featureCount()} unidades dibujadas"
            if sin_geom:
                detalle += (f"; {len(sin_geom)} de los {len(df)} resultados no "
                            f"tienen geometría ({', '.join(sin_geom[:4])})")
            self.iface.messageBar().pushSuccess("Q-CensosBo", detalle + ".")
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
        w.done.connect(
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
            detalle = (f"Mapa generado: {total_geom} unidades del municipio en "
                       f"{n_datos} capa(s), {total_geom - sin_dato} con dato")
            if sin_dato > 0:
                detalle += f" y {sin_dato} sin ficha (sin dato)"
            self.iface.messageBar().pushSuccess("Q-CensosBo", detalle + ".")
        except Exception as exc:
            self.iface.messageBar().pushCritical("Q-CensosBo", f"Error: {exc}")
