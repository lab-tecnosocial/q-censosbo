def classFactory(iface):
    from .censosbo_plugin import CensosBolivaPlugin
    return CensosBolivaPlugin(iface)
