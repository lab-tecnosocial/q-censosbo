import os
from qgis.PyQt.QtWidgets import QAction
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtCore import Qt
from qgis.core import QgsApplication


class CensosBolivaPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.plugin_dir = os.path.dirname(__file__)
        self.action = None
        self.panel = None
        self._provider = None

    def initGui(self):
        icon_path = os.path.join(self.plugin_dir, "icon.png")
        icon = QIcon(icon_path) if os.path.exists(icon_path) else QIcon()
        self.action = QAction(icon, "Q-CensosBo", self.iface.mainWindow())
        self.action.setToolTip("Abrir explorador Q-CensosBo")
        self.action.triggered.connect(self._toggle_panel)

        self.iface.addPluginToMenu("Q-CensosBo", self.action)
        self.iface.addToolBarIcon(self.action)

        from .panel.dock_panel import CensosBOPanel
        self.panel = CensosBOPanel(self.iface)
        self.iface.addDockWidget(Qt.RightDockWidgetArea, self.panel)
        self.panel.hide()

        # Registrar proveedor en el Toolbox de QGIS Processing
        from .processing.provider import CensosBoProvider
        self._provider = CensosBoProvider()
        QgsApplication.processingRegistry().addProvider(self._provider)

    def unload(self):
        self.iface.removePluginMenu("Q-CensosBo", self.action)
        self.iface.removeToolBarIcon(self.action)
        if self.panel:
            self.iface.removeDockWidget(self.panel)
            self.panel.deleteLater()
            self.panel = None
        if self._provider:
            QgsApplication.processingRegistry().removeProvider(self._provider)
            self._provider = None
        try:
            from .core.query_engine import cleanup as duckdb_cleanup
            duckdb_cleanup()
        except Exception:
            pass

    def _toggle_panel(self):
        if self.panel:
            if self.panel.isHidden():
                self.panel.show()
            else:
                self.panel.hide()
