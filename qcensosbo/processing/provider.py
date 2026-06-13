from qgis.core import QgsProcessingProvider


class CensosBoProvider(QgsProcessingProvider):

    def id(self):
        return "qcensosbo"

    def name(self):
        return "Q-CensosBo"

    def longName(self):
        return "Q-CensosBo — microdatos de los censos de Bolivia"

    def icon(self):
        return QgsProcessingProvider.icon(self)

    def loadAlgorithms(self):
        from .algorithms.calcular_indicador import CalcularIndicadorAlgorithm
        self.addAlgorithm(CalcularIndicadorAlgorithm())
