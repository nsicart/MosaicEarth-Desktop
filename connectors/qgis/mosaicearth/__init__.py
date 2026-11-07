def classFactory(iface):
    from .plugin import MosaicEarthPlugin
    return MosaicEarthPlugin(iface)
