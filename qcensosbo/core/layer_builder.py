"""
Crea capas vectoriales QGIS a partir de datos censales agregados y geometrías GeoJSON bundled.

El GeoJSON resultante se guarda en una carpeta de caché ESTABLE del plugin
(`~/.censosbo_qgis/capas/`), no en el temp del sistema: así, si el usuario guarda
el proyecto QGIS, la capa sigue apuntando a un archivo que persiste entre sesiones.
"""

import json
import re
import uuid
from pathlib import Path

from .data_loader import cache_dir


DATA_DIR = Path(__file__).parent.parent / "data"

GEO_FILES = {
    "departamento": DATA_DIR / "geo_departamentos.geojson",
    "municipio":    DATA_DIR / "geo_municipios.geojson",
}


def _get_geo_code(props, nivel):
    """
    Construye el código geográfico de join a partir de las propiedades del GeoJSON.

    Departamento: idep zero-padded a 2 dígitos  → "01"…"09"
    Municipio:    idep(2) + iprov(2) + imun(2)  → "010101" (código nacional completo)
    """
    if nivel == "departamento":
        return str(props.get("idep", "")).strip().zfill(2)
    else:
        idep  = str(props.get("idep",  "")).strip().zfill(2)
        iprov = str(props.get("iprov", "")).strip().zfill(2)
        imun  = str(props.get("imun",  "")).strip().zfill(2)
        return idep + iprov + imun


_nombres_cache = {}


def geo_nombres(nivel):
    """Mapeo {geo_code: nombre} leído del GeoJSON bundled (cacheado en memoria).

    Reutiliza la geometría que ya viene con el plugin como única fuente de
    verdad de los nombres: departamento → `nombre_dep`, municipio → `nombre_mun`.
    Así el resumen puede mostrar un ranking legible por nombre, igual a cualquier
    nivel, sin descargar ni duplicar un archivo aparte.
    """
    if nivel in _nombres_cache:
        return _nombres_cache[nivel]
    path = GEO_FILES.get(nivel)
    campo = "nombre_dep" if nivel == "departamento" else "nombre_mun"
    result = {}
    if path and path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                geojson = json.load(f)
            for feature in geojson.get("features", []):
                props = feature.get("properties", {})
                nombre = props.get(campo)
                if nombre:
                    result[_get_geo_code(props, nivel)] = str(nombre)
        except Exception:
            result = {}
    _nombres_cache[nivel] = result
    return result


def crear_capa(df_agregado, nivel, nombre_capa, iface=None,
               departamento=None, is_categorical=False, clasificacion="jenks",
               value_labels=None):
    """
    Une df_agregado con geometrías GeoJSON y crea una QgsVectorLayer en QGIS.

    - df_agregado:  DataFrame con columnas [geo_code, geo_nombre, valor]
    - nivel:        "departamento" | "municipio"
    - nombre_capa:  str — nombre visible en el panel de capas
    - iface:        QgisInterface (para zoom automático)
    - departamento: código "01"…"09" para filtrar municipios (solo nivel municipio)
    """
    geo_path = GEO_FILES.get(nivel)
    if not geo_path or not geo_path.exists():
        raise FileNotFoundError(
            f"Geometría no encontrada: {geo_path}\n"
            "Verifica que los archivos geo_departamentos.geojson y geo_municipios.geojson "
            "estén en la carpeta data/ del plugin."
        )

    # Construir lookup: geo_code → valor
    lookup  = {str(r["geo_code"]): r["valor"]      for _, r in df_agregado.iterrows()}
    nombres = {str(r["geo_code"]): r.get("geo_nombre", r["geo_code"])
               for _, r in df_agregado.iterrows()}

    with open(geo_path, encoding="utf-8") as f:
        geojson = json.load(f)

    # Filtrar y enriquecer features
    features_out = []
    for feature in geojson.get("features", []):
        props = feature.get("properties", {})
        code = _get_geo_code(props, nivel)

        # Filtrar por departamento cuando nivel = municipio
        if departamento and nivel == "municipio":
            if str(props.get("idep", "")).strip().zfill(2) != str(departamento).zfill(2):
                continue

        props["valor_censo"] = lookup.get(code)
        props["nombre_geo"]  = nombres.get(code, props.get("nombre_dep", props.get("nombre_mun", code)))
        feature["properties"] = props
        features_out.append(feature)

    geojson["features"] = features_out

    # Escribir en la carpeta de caché estable del plugin (no en el temp del
    # sistema). Nombre único por generación para no pisar una capa ya cargada.
    capas_dir = cache_dir() / "capas"
    capas_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", nombre_capa) or "capa"
    out_path = capas_dir / f"{safe}_{uuid.uuid4().hex[:8]}.geojson"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False)

    from qgis.core import QgsVectorLayer, QgsProject

    layer = QgsVectorLayer(str(out_path), nombre_capa, "ogr")
    if not layer.isValid():
        try:
            out_path.unlink()
        except OSError:
            pass
        raise RuntimeError(f"No se pudo cargar la capa: {out_path}")

    if is_categorical:
        _apply_categorical_style(layer, "valor_censo", labels=value_labels)
    else:
        _apply_graduated_style(layer, "valor_censo", clasificacion=clasificacion)

    QgsProject.instance().addMapLayer(layer)

    if iface:
        iface.mapCanvas().setExtent(layer.extent())
        iface.mapCanvas().refresh()

    return layer


def _apply_graduated_style(layer, field_name, n_classes=5, clasificacion="jenks"):
    """Aplica simbología graduada con el método de clasificación elegido."""
    try:
        from qgis.core import (
            QgsGraduatedSymbolRenderer,
            QgsStyle,
            QgsClassificationJenks,
            QgsClassificationQuantile,
            QgsClassificationEqualInterval,
            QgsClassificationStandardDeviation,
            QgsRendererRangeLabelFormat,
        )
        from qgis.PyQt.QtGui import QColor

        method_map = {
            "jenks":    QgsClassificationJenks,
            "quantile": QgsClassificationQuantile,
            "equal":    QgsClassificationEqualInterval,
            "stddev":   QgsClassificationStandardDeviation,
        }
        method_cls = method_map.get(clasificacion, QgsClassificationJenks)

        style = QgsStyle.defaultStyle()
        ramp = style.colorRamp("Reds")
        if not ramp:
            from qgis.core import QgsGradientColorRamp
            ramp = QgsGradientColorRamp(QColor("#fee5d9"), QColor("#a50f15"))

        # Evitar clases duplicadas ("24–24" repetido) cuando hay muy pocos
        # valores distintos: no tiene sentido pedir más clases que valores únicos.
        distinct = {
            feat[field_name] for feat in layer.getFeatures()
            if feat[field_name] is not None
        }
        n_classes = max(1, min(n_classes, len(distinct)))

        renderer = QgsGraduatedSymbolRenderer(field_name)
        renderer.setClassificationMethod(method_cls())
        renderer.updateClasses(layer, n_classes)
        renderer.updateColorRamp(ramp)

        fmt = QgsRendererRangeLabelFormat()
        fmt.setFormat("%1 – %2")
        fmt.setPrecision(2)
        fmt.setTrimTrailingZeroes(True)
        renderer.setLabelFormat(fmt)

        layer.setRenderer(renderer)
        layer.triggerRepaint()
    except Exception:
        pass


def _apply_categorical_style(layer, field_name, labels=None):
    """Aplica simbología categórica (colores distintos por valor) para variables de moda.

    labels: dict {codigo: etiqueta}. Si se provee, la leyenda muestra el nombre
    legible (p.ej. "Quechua") en vez del código crudo ("1").
    """
    labels = labels or {}
    try:
        from .query_engine import normalize_code
    except Exception:
        def normalize_code(s):
            return str(s)
    norm_labels = {normalize_code(k): v for k, v in labels.items()}

    def _legend(val):
        lbl = norm_labels.get(normalize_code(val))
        return f"{val} — {lbl}" if lbl else str(val)

    try:
        from qgis.core import (
            QgsCategorizedSymbolRenderer,
            QgsRendererCategory,
            QgsSymbol,
            QgsStyle,
        )
        from qgis.PyQt.QtGui import QColor

        # Recopilar valores únicos del campo
        values = sorted({
            str(feat[field_name])
            for feat in layer.getFeatures()
            if feat[field_name] is not None
        })

        style = QgsStyle.defaultStyle()
        # Intentar paleta cualitativa; si no existe, generar colores por tono
        ramp = style.colorRamp("Paired") or style.colorRamp("Set1")

        categories = []
        n = max(len(values), 1)
        for i, val in enumerate(values):
            if ramp:
                color = ramp.color(i / (n - 1) if n > 1 else 0)
            else:
                color = QColor.fromHsvF(i / n, 0.65, 0.85)
            symbol = QgsSymbol.defaultSymbol(layer.geometryType())
            symbol.setColor(color)
            categories.append(QgsRendererCategory(val, symbol, _legend(val)))

        renderer = QgsCategorizedSymbolRenderer(field_name, categories)
        layer.setRenderer(renderer)
        layer.triggerRepaint()
    except Exception:
        pass
