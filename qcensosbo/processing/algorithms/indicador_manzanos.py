"""
Algoritmo de Processing: Indicador por manzano/comunidad (CPV-2024).

El nivel más fino del censo, para usar en modelos gráficos, procesamiento por
lotes (p. ej. el mismo indicador en 20 municipios) y desde la consola de QGIS.

Comparte toda la lógica con el panel: el catálogo y las expresiones salen de
`core.fichas`, la agregación de `core.aggregator` y las geometrías de
`fichas.geometrias`. Aquí solo se traducen parámetros y se escribe el sink.
"""

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingParameterEnum,
    QgsProcessingParameterString,
    QgsProcessingParameterFeatureSink,
    QgsFeatureSink,
    QgsFeature,
    QgsFields,
    QgsField,
    QgsGeometry,
    QgsWkbTypes,
    QgsCoordinateReferenceSystem,
)
try:
    from ...core import fichas
    from ...core.compat import TIPO_DECIMAL, TIPO_TEXTO
except ImportError:                                   # ejecución fuera del plugin
    from qcensosbo.core import fichas
    from qcensosbo.core.compat import TIPO_DECIMAL, TIPO_TEXTO


_TABLAS  = ["fichas", "unidades"]
_AREAS   = ["urbana (manzanos)", "rural (comunidades)"]
_AREA_KEYS = ["urbana", "rural"]
_MEDIDAS = ["Total (conteo)", "% del total del bloque"]
_MEDIDA_KEYS = ["total", "porcentaje"]


class IndicadorManzanosAlgorithm(QgsProcessingAlgorithm):

    TABLA     = "TABLA"
    MUNICIPIO = "MUNICIPIO"
    AREA      = "AREA"
    VARIABLE  = "VARIABLE"
    MEDIDA    = "MEDIDA"
    OUTPUT    = "OUTPUT"

    def name(self):
        return "indicador_manzanos"

    def displayName(self):
        return "Indicador por manzano/comunidad (CPV-2024)"

    def group(self):
        return "Análisis censal"

    def groupId(self):
        return "analisis_censal"

    def shortHelpString(self):
        return (
            "Genera una capa con un indicador del CPV-2024 por manzano urbano o "
            "comunidad rural, para un municipio.\n\n"
            "• Municipio: código nacional de 6 dígitos (idep+iprov+imun), "
            "p. ej. 020105 para El Alto.\n"
            "• Área: los manzanos son polígonos; las comunidades, puntos. Cada "
            "ejecución produce una sola geometría, así que se elige una.\n"
            "• Variable: nombre del indicador de la ficha (p. ej. serv_agua_caneria) "
            "o de la tabla de unidades (personas, viviendas, ficha).\n"
            "• Medida: el conteo, o su porcentaje sobre el total del bloque "
            "temático (p. ej. serv_agua_caneria / serv_agua_total).\n\n"
            "Las unidades cuya ficha el INE reserva por poca población salen con "
            "valor_censo nulo: a este nivel eso solo deja huecos en el mapa.\n\n"
            "Si lo que quieres es el indicador por MUNICIPIO o DEPARTAMENTO, no "
            "sumes estas fichas: cubren el 92 % de la población y de forma desigual "
            "(del 85 % en Oruro al 94 % en La Paz), así que el resultado quedaría "
            "deformado. Usa «Calcular indicador censal» con los microdatos, que "
            "cubren el 100 %. Todo lo de la ficha se puede recalcular desde ellos.\n\n"
            "La capa no lleva simbología: aplícala en QGIS o usa "
            "el panel del plugin, que sí la aplica."
        )

    def createInstance(self):
        return IndicadorManzanosAlgorithm()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterEnum(
            self.TABLA, "Tabla", options=_TABLAS, defaultValue=0,
        ))
        self.addParameter(QgsProcessingParameterString(
            self.MUNICIPIO, "Municipio (código de 6 dígitos, p. ej. 020105)",
            defaultValue="",
        ))
        self.addParameter(QgsProcessingParameterEnum(
            self.AREA, "Área", options=_AREAS, defaultValue=0,
        ))
        self.addParameter(QgsProcessingParameterString(
            self.VARIABLE, "Indicador (nombre de variable)", defaultValue="",
        ))
        self.addParameter(QgsProcessingParameterEnum(
            self.MEDIDA, "Medida", options=_MEDIDAS, defaultValue=0,
        ))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, "Capa de salida",
        ))

    def processAlgorithm(self, parameters, context, feedback):
        try:
            from ...core.query_engine import get_parquet_urls, duckdb_available
            from ...core.aggregator import agregar_expresion
        except ImportError:
            from qcensosbo.core.query_engine import get_parquet_urls, duckdb_available
            from qcensosbo.core.aggregator import agregar_expresion

        tabla     = _TABLAS[self.parameterAsEnum(parameters, self.TABLA, context)]
        municipio = self.parameterAsString(parameters, self.MUNICIPIO, context).strip()
        area      = _AREA_KEYS[self.parameterAsEnum(parameters, self.AREA, context)]
        variable  = self.parameterAsString(parameters, self.VARIABLE, context).strip()
        medida    = _MEDIDA_KEYS[self.parameterAsEnum(parameters, self.MEDIDA, context)]

        if not municipio.isdigit() or len(municipio) != 6:
            raise Exception(
                "El municipio debe ser el código nacional de 6 dígitos "
                "(idep+iprov+imun), p. ej. 020105 para El Alto."
            )
        if not variable:
            raise Exception(
                "Indica el nombre del indicador. Los de la ficha están listados en "
                "data/dicc_fichas.csv del plugin (p. ej. serv_agua_caneria)."
            )
        if not duckdb_available():
            raise Exception(
                "El motor de consulta (DuckDB) no está disponible. Abre una vez "
                "el panel de Q-CensosBo (instala DuckDB automáticamente) y revisa "
                "tu conexión a internet, luego vuelve a ejecutar este algoritmo."
            )

        sql_expr = fichas.sql_valor(tabla, variable, medida)

        feedback.setProgressText("Consultando el indicador…")
        feedback.setProgress(5)
        urls = get_parquet_urls(2024, tabla)
        df = agregar_expresion(urls, "unidad", sql_expr,
                               municipio=municipio, area=area)
        feedback.setProgress(45)
        if feedback.isCanceled():
            return {}

        feedback.setProgressText("Obteniendo geometrías…")
        geoms = fichas.geometrias(
            municipio, area,
            progress_cb=lambda p: feedback.setProgress(45 + int(p * 0.35)),
        )
        if not geoms:
            raise Exception(
                f"El municipio {municipio} no tiene unidades del área «{area}». "
                "Revisa el código, o prueba la otra área."
            )
        feedback.setProgress(80)

        lookup = {str(r["geo_code"]): r["valor"] for _, r in df.iterrows()}

        fields = QgsFields()
        fields.append(QgsField("codigo", TIPO_TEXTO))
        fields.append(QgsField("nombre_geo", TIPO_TEXTO))
        fields.append(QgsField("area", TIPO_TEXTO))
        fields.append(QgsField("valor_censo", TIPO_DECIMAL))

        wkb_type = (QgsWkbTypes.Type.MultiPolygon if area == "urbana"
                    else QgsWkbTypes.Type.Point)
        (sink, dest_id) = self.parameterAsSink(
            parameters, self.OUTPUT, context, fields, wkb_type,
            QgsCoordinateReferenceSystem("EPSG:4326"),
        )

        area_lbl = "Urbana" if area == "urbana" else "Rural"
        total = len(geoms)
        for i, (codigo, nombre, wkb) in enumerate(geoms):
            if feedback.isCanceled():
                break
            geom = QgsGeometry()
            geom.fromWkb(bytes(wkb))
            if geom.isNull():
                continue
            valor = lookup.get(str(codigo))
            feat = QgsFeature(fields)
            feat.setGeometry(geom)
            feat.setAttributes([str(codigo), str(nombre or ""), area_lbl,
                                None if valor is None else float(valor)])
            sink.addFeature(feat, QgsFeatureSink.Flag.FastInsert)
            feedback.setProgress(80 + int(i / max(total, 1) * 20))

        sin_dato = sum(1 for codigo, _, _ in geoms if str(codigo) not in lookup)
        if sin_dato:
            feedback.pushInfo(
                f"{sin_dato} de {total} unidades sin dato: el INE reserva la "
                "ficha de las unidades con poca población."
            )
        feedback.setProgress(100)
        return {self.OUTPUT: dest_id}
