"""
Algoritmo de Processing: Calcular indicador censal.

Aparece en el Toolbox de QGIS bajo "Q-CensosBo".
Permite usar los datos censales en modelos gráficos, procesamiento
por lotes y desde la consola Python de QGIS.
"""

from pathlib import Path

from qgis.core import (
    QgsProcessingAlgorithm,
    QgsProcessingParameterEnum,
    QgsProcessingParameterString,
    QgsProcessingParameterFeatureSink,
    QgsFeatureSink,
    QgsFeature,
    QgsFields,
    QgsField,
    QgsWkbTypes,
    QgsCoordinateReferenceSystem,
    QgsVectorLayer,
)
try:
    from ...core.compat import TIPO_DECIMAL, TIPO_TEXTO
except ImportError:                                   # ejecución fuera del plugin
    from qcensosbo.core.compat import TIPO_DECIMAL, TIPO_TEXTO


DATA_DIR = Path(__file__).parent.parent.parent / "data"

_ANIOS    = [2024, 2012, 2001, 1992, 1976]
_TABLAS   = ["personas", "viviendas", "emigracion", "mortalidad"]
_NIVELES  = ["departamento", "municipio"]
_AGGS     = ["Conteo total", "Media", "Suma", "Mediana",
             "Desviación estándar", "Moda", "% de categoría"]
_AGG_KEYS = ["__count__", "mean", "sum", "median", "std", "mode", "pct_category"]


class CalcularIndicadorAlgorithm(QgsProcessingAlgorithm):

    ANIO         = "ANIO"
    TABLA        = "TABLA"
    NIVEL        = "NIVEL"
    VARIABLE     = "VARIABLE"
    AGREGACION   = "AGREGACION"
    CATEGORIA    = "CATEGORIA"
    EXPRESION_SQL = "EXPRESION_SQL"
    OUTPUT       = "OUTPUT"

    def name(self):
        return "calcular_indicador"

    def displayName(self):
        return "Calcular indicador censal"

    def group(self):
        return "Análisis censal"

    def groupId(self):
        return "analisis_censal"

    def shortHelpString(self):
        return (
            "Agrega microdatos de los Censos de Bolivia (1976–2024) por departamento "
            "o municipio y genera una capa vectorial con el campo valor_censo.\n\n"
            "Parámetros principales:\n"
            "• Año / Tabla / Nivel geográfico\n"
            "• Variable y tipo de agregación (conteo, media, suma, % categoría)\n"
            "• Expresión SQL (opcional): fórmula DuckDB libre, ej. AVG(p26_edad)\n\n"
            "Si se escribe una Expresión SQL, los campos Variable y Agregación se ignoran. "
            "La expresión SQL requiere DuckDB (se instala automáticamente al abrir el plugin).\n\n"
            "«% de categoría» se calcula sobre los CASOS VÁLIDOS de la variable "
            "(los registros con valor nulo quedan fuera del denominador), igual que "
            "el resto de agregaciones. Muchas preguntas del censo solo aplican a un "
            "subgrupo, así que el resultado es el porcentaje entre quienes "
            "respondieron, no entre toda la población.\n\n"
            "1976 solo admite el nivel departamental (ese censo usa cantón, no "
            "municipio). La cartografía municipal es la de los 343 municipios del "
            "CPV-2024, así que 2024 y 2001 se mapean completos; al usar censos "
            "anteriores puede haber códigos que no existan en la división actual, y "
            "el log lo indica.\n\n"
            "La capa sale sin simbología: aplícala en QGIS, o usa el panel del "
            "plugin, que sí la aplica. Para el nivel de manzano y comunidad del "
            "CPV-2024, usa «Indicador por manzano/comunidad»."
        )

    def createInstance(self):
        return CalcularIndicadorAlgorithm()

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterEnum(
            self.ANIO, "Año del censo",
            options=[str(a) for a in _ANIOS],
            defaultValue=0,
        ))
        self.addParameter(QgsProcessingParameterEnum(
            self.TABLA, "Tabla",
            options=_TABLAS,
            defaultValue=0,
        ))
        self.addParameter(QgsProcessingParameterEnum(
            self.NIVEL, "Nivel geográfico",
            options=_NIVELES,
            defaultValue=0,
        ))
        self.addParameter(QgsProcessingParameterString(
            self.VARIABLE, "Variable (nombre de columna)",
            defaultValue="",
            optional=True,
        ))
        self.addParameter(QgsProcessingParameterEnum(
            self.AGREGACION, "Tipo de agregación",
            options=_AGGS,
            defaultValue=0,
            optional=True,
        ))
        self.addParameter(QgsProcessingParameterString(
            self.CATEGORIA, "Categoría (solo para '% de categoría')",
            defaultValue="",
            optional=True,
        ))
        self.addParameter(QgsProcessingParameterString(
            self.EXPRESION_SQL,
            "Expresión SQL avanzada (opcional, reemplaza Variable + Agregación)",
            multiLine=True,
            defaultValue="",
            optional=True,
        ))
        self.addParameter(QgsProcessingParameterFeatureSink(
            self.OUTPUT, "Capa de salida",
        ))

    def processAlgorithm(self, parameters, context, feedback):
        anio      = _ANIOS[self.parameterAsEnum(parameters, self.ANIO, context)]
        tabla     = _TABLAS[self.parameterAsEnum(parameters, self.TABLA, context)]
        nivel     = _NIVELES[self.parameterAsEnum(parameters, self.NIVEL, context)]
        variable  = self.parameterAsString(parameters, self.VARIABLE, context).strip()
        agg_idx   = self.parameterAsEnum(parameters, self.AGREGACION, context)
        agg       = _AGG_KEYS[agg_idx] if agg_idx is not None else "__count__"
        category  = self.parameterAsString(parameters, self.CATEGORIA, context).strip() or None
        sql_expr  = self.parameterAsString(parameters, self.EXPRESION_SQL, context).strip() or None

        if not variable:
            variable = "__count__"
            agg = "__count__"

        feedback.setProgressText("Obteniendo datos censales…")

        try:
            from ...core.query_engine import get_parquet_urls, duckdb_available
            from ...core.aggregator import agregar_datos, agregar_expresion
            from ...core.universos import universo_sql, cobertura_vivienda
        except ImportError:
            from qcensosbo.core.query_engine import get_parquet_urls, duckdb_available
            from qcensosbo.core.aggregator import agregar_datos, agregar_expresion
            from qcensosbo.core.universos import universo_sql, cobertura_vivienda

        feedback.setProgress(5)
        urls = get_parquet_urls(anio, tabla)
        # Qué filas de la tabla son su universo (ver core/universos.py). En
        # viviendas descuenta los registros de personas en la calle o en tránsito.
        universo = universo_sql(anio, tabla)
        nota_universo = cobertura_vivienda(anio, tabla)
        if nota_universo:
            feedback.pushInfo(nota_universo)
        feedback.setProgress(10)

        if not duckdb_available():
            raise Exception(
                "El motor de consulta (DuckDB) no está disponible. Abre una vez "
                "el panel de Q-CensosBo (instala DuckDB automáticamente) y revisa "
                "tu conexión a internet, luego vuelve a ejecutar este algoritmo."
            )

        if sql_expr:
            feedback.setProgressText("Ejecutando expresión SQL…")
            df = agregar_expresion(urls, nivel, sql_expr, universo_tabla=universo)
        else:
            feedback.setProgressText("Consultando datos remotos…")
            df = agregar_datos(urls, nivel, variable, agg, category,
                               universo_tabla=universo)

        feedback.setProgress(85)
        if feedback.isCanceled():
            return {}

        # Construir lookup geo_code → valor
        lookup = {str(r["geo_code"]): r["valor"] for _, r in df.iterrows()}

        # Los códigos que no tienen polígono no se pintan: decirlo en el log en
        # vez de dejar que el conteo de la capa no cuadre con el de los datos.
        try:
            from ...core.layer_builder import cobertura_geo
        except ImportError:
            from qcensosbo.core.layer_builder import cobertura_geo
        n_map, sin_geom = cobertura_geo(lookup.keys(), nivel)
        if sin_geom:
            feedback.pushWarning(
                f"{len(sin_geom)} de los {len(lookup)} resultados no tienen "
                f"geometría en el plugin y no se dibujarán: "
                f"{', '.join(sin_geom)}."
            )

        # Cargar GeoJSON bundled
        geo_key = "departamentos" if nivel == "departamento" else "municipios"
        geo_path = DATA_DIR / f"geo_{geo_key}.geojson"
        if not geo_path.exists():
            raise Exception(
                f"Geometría no encontrada: {geo_path}\n"
                "Verifica que los archivos GeoJSON estén en la carpeta data/ del plugin."
            )

        # Usar QgsVectorLayer temporal para leer el GeoJSON (evita parsear manualmente)
        src_layer = QgsVectorLayer(str(geo_path), "tmp_geo", "ogr")
        if not src_layer.isValid():
            raise Exception(f"No se pudo cargar la geometría: {geo_path}")

        # Código geográfico de join: departamento = idep(2);
        # municipio = idep(2)+iprov(2)+imun(2) (igual que en layer_builder).
        def geo_code_of(feat):
            idep = str(feat["idep"]).strip().zfill(2)
            if nivel == "departamento":
                return idep
            iprov = str(feat["iprov"]).strip().zfill(2)
            imun  = str(feat["imun"]).strip().zfill(2)
            return idep + iprov + imun

        # Definir campos de salida
        fields = QgsFields()
        for f in src_layer.fields():
            fields.append(f)
        fields.append(QgsField("valor_censo", TIPO_DECIMAL))
        fields.append(QgsField("geo_code", TIPO_TEXTO))

        crs = QgsCoordinateReferenceSystem("EPSG:4326")
        (sink, dest_id) = self.parameterAsSink(
            parameters, self.OUTPUT, context,
            fields, QgsWkbTypes.Type.MultiPolygon, crs,
        )

        # Escribir features al sink
        total = src_layer.featureCount()
        for i, feat in enumerate(src_layer.getFeatures()):
            if feedback.isCanceled():
                break
            code = geo_code_of(feat)
            valor = lookup.get(code)

            out_feat = QgsFeature(fields)
            out_feat.setGeometry(feat.geometry())
            # Copiar atributos originales
            for j, attr in enumerate(feat.attributes()):
                out_feat.setAttribute(j, attr)
            out_feat["valor_censo"] = valor
            out_feat["geo_code"] = code
            sink.addFeature(out_feat, QgsFeatureSink.Flag.FastInsert)

            feedback.setProgress(85 + int(i / max(total, 1) * 15))

        feedback.setProgress(100)
        return {self.OUTPUT: dest_id}
