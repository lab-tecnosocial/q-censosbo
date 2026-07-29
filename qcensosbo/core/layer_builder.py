"""
Crea capas vectoriales QGIS a partir de datos censales agregados.

Dos fuentes de geometría, según el nivel:
  - departamento y municipio → GeoJSON empaquetado con el plugin (`data/`), que
    se enriquece con el valor y se escribe como GeoJSON.
  - manzano y comunidad → geometrías WKB del release de fichas (ver `fichas.py`),
    que se materializan en un GeoPackage: un municipio son miles de polígonos y
    GeoJSON ahí es lento y enorme.

En ambos casos el archivo se guarda en una carpeta de caché ESTABLE del plugin
(`~/.censosbo_qgis/capas/`), no en el temp del sistema: así, si el usuario guarda
el proyecto QGIS, la capa sigue apuntando a un archivo que persiste entre sesiones.
"""

import json
import re
import uuid
from pathlib import Path

from . import log
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


_superficies_cache = {}


def geo_superficies(nivel):
    """{geo_code: km²} desde el GeoJSON empaquetado, para calcular densidades.

    `superficie_km2` la trae `censosbo::geo_municipios` (1.6.0+), así que el plugin
    no tiene que reproyectar y medir polígonos: usa el área que la fuente ya declara.
    A nivel departamental se **suma la de sus municipios**, que es consistente porque
    `geo_departamentos` se deriva por disolución de los municipales.

    Ojo con lo que mide: la suma nacional da unos 1.063.500 km², frente a los
    ~1.098.600 de la superficie oficial de Bolivia. La diferencia son los grandes
    cuerpos de agua y salares que no pertenecen a ningún municipio (Titicaca, Poopó,
    Uru Uru, Salar de Uyuni). Es decir, esto es **superficie municipal**, y las
    densidades que salgan de aquí son algo mayores que las oficiales en los
    departamentos con lagos o salares grandes. El panel lo dice.

    Devuelve `{}` si el GeoJSON no trae la columna (una versión anterior de los
    datos empaquetados), y entonces la densidad simplemente no se ofrece.
    """
    if nivel in _superficies_cache:
        return _superficies_cache[nivel]
    path = GEO_FILES.get("municipio")
    resultado = {}
    if path and path.exists():
        try:
            with open(path, encoding="utf-8") as f:
                geojson = json.load(f)
            for feature in geojson.get("features", []):
                props = feature.get("properties", {})
                km2 = props.get("superficie_km2")
                if km2 is None:
                    continue
                clave = (str(props.get("idep", "")).strip().zfill(2)
                         if nivel == "departamento"
                         else _get_geo_code(props, "municipio"))
                resultado[clave] = resultado.get(clave, 0.0) + float(km2)
        except Exception as exc:
            log.aviso("No se pudieron leer las superficies municipales", exc)
            resultado = {}
    _superficies_cache[nivel] = resultado
    return resultado


def cobertura_geo(codigos, nivel, departamento=None):
    """(mapeados, sin_geometria) de un conjunto de códigos frente al GeoJSON.

    Desde censosbo 1.6.0 la cartografía trae los **343** municipios del CPV-2024, y
    2024 y 2001 se mapean completos. Sigue haciendo falta porque los censos
    anteriores usan divisiones distintas: los códigos de 1992 o 1976 no tienen por
    qué existir en la división actual, y sin esto se perderían en silencio (el
    resumen diría 343 unidades y el mapa pintaría menos).
    """
    disponibles = set(geo_nombres(nivel))
    if departamento and nivel == "municipio":
        prefijo = str(departamento).zfill(2)
        disponibles = {c for c in disponibles if c.startswith(prefijo)}
    pedidos = {str(c) for c in codigos}
    mapeados = pedidos & disponibles
    return len(mapeados), sorted(pedidos - disponibles)


def municipios_por_depto(idep):
    """Municipios de un departamento: [(nombre, geo_code de 6 dígitos)], ordenados.

    Sale del mismo GeoJSON empaquetado que ya provee los nombres, así el selector
    de municipio no necesita red ni un archivo aparte.
    """
    prefijo = str(idep).zfill(2)
    nombres = geo_nombres("municipio")
    items = [(nombre, code) for code, nombre in nombres.items()
             if code.startswith(prefijo)]
    return sorted(items, key=lambda t: t[0])


def _capas_dir():
    path = cache_dir() / "capas"
    path.mkdir(parents=True, exist_ok=True)
    return path


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
    lookup = {str(r["geo_code"]): r["valor"] for _, r in df_agregado.iterrows()}
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
        props["nombre_geo"] = nombres.get(
            code, props.get("nombre_dep", props.get("nombre_mun", code)))
        feature["properties"] = props
        features_out.append(feature)

    geojson["features"] = features_out

    # Escribir en la carpeta de caché estable del plugin (no en el temp del
    # sistema). Nombre único por generación para no pisar una capa ya cargada.
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", nombre_capa) or "capa"
    out_path = _capas_dir() / f"{safe}_{uuid.uuid4().hex[:8]}.geojson"
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


CLASIFICACIONES = ("jenks", "quantile", "equal", "stddev")


def _classification_method(clasificacion):
    """Instancia del método de clasificación de QGIS ('jenks' por defecto)."""
    from qgis.core import (
        QgsClassificationJenks,
        QgsClassificationQuantile,
        QgsClassificationEqualInterval,
        QgsClassificationStandardDeviation,
    )
    method_map = {
        "jenks":    QgsClassificationJenks,
        "quantile": QgsClassificationQuantile,
        "equal":    QgsClassificationEqualInterval,
        "stddev":   QgsClassificationStandardDeviation,
    }
    return method_map.get(clasificacion, QgsClassificationJenks)()


def class_bounds(values, clasificacion="jenks", n_classes=5):
    """Cortes de clase [(inferior, superior), …] con el MÉTODO REAL de QGIS.

    Es la única fuente de verdad de la clasificación: la usan el renderer del mapa
    y el histograma del resumen del panel, para que la vista previa muestre
    exactamente los rangos que va a tener la leyenda.

    Depura las clases degeneradas (`inferior == superior`) que Jenks produce con
    muestras pequeñas: con 9 departamentos devuelve dos rangos "26,45 – 26,45"
    idénticos, que en la leyenda salen repetidos y vacíos. Al quitarlos se baja el
    borde inferior de la primera clase superviviente al mínimo real, de modo que
    la cobertura de valores no cambia.

    Retorna [] si no hay valores utilizables.
    """
    vals = [float(v) for v in values if v is not None]
    if not vals:
        return []
    vmin, vmax = min(vals), max(vals)
    if vmin == vmax:
        return [(vmin, vmax)]

    n = max(1, min(int(n_classes), len(set(vals))))
    try:
        rangos = _classification_method(clasificacion).classes(vals, n)
        pares = [(r.lowerBound(), r.upperBound()) for r in rangos]
    except Exception:
        pares = []
    if not pares:
        paso = (vmax - vmin) / n
        pares = [(vmin + i * paso, vmin + (i + 1) * paso) for i in range(n)]

    limpios = [(lo, hi) for lo, hi in pares if hi > lo]
    if not limpios:
        return [(vmin, vmax)]
    # Recuperar el borde inferior si se descartaron clases degeneradas al inicio.
    lo0, hi0 = limpios[0]
    limpios[0] = (min(lo0, vmin), hi0)
    return limpios


def bins_from_bounds(bounds):
    """Bordes de histograma [b0, b1, …] a partir de los cortes de `class_bounds`."""
    if not bounds:
        return []
    edges = [bounds[0][0]] + [hi for _, hi in bounds]
    # np.histogram exige bordes estrictamente crecientes.
    salida = [edges[0]]
    for e in edges[1:]:
        if e > salida[-1]:
            salida.append(e)
    return salida if len(salida) >= 2 else []


def _graduated_renderer(layer, field_name, n_classes=5, clasificacion="jenks"):
    """Construye el renderer graduado de una capa (o None si algo falla).

    Los cortes salen de `class_bounds`, así que la leyenda nunca muestra clases
    repetidas y coincide con el histograma que el panel enseñó al consultar.
    """
    try:
        from qgis.core import (
            NULL,
            QgsGraduatedSymbolRenderer,
            QgsRendererRange,
            QgsStyle,
            QgsSymbol,
            QgsRendererRangeLabelFormat,
        )
        from qgis.PyQt.QtGui import QColor

        style = QgsStyle.defaultStyle()
        ramp = style.colorRamp("Reds")
        if not ramp:
            from qgis.core import QgsGradientColorRamp
            ramp = QgsGradientColorRamp(QColor("#fee5d9"), QColor("#a50f15"))

        # Un campo vacío llega como QVariant nulo, NO como None: `is not None` no
        # lo descarta. A nivel de manzano importa, porque casi la mitad de las
        # unidades no tiene ficha.
        valores = [feat[field_name] for feat in layer.getFeatures()
                   if feat[field_name] != NULL]
        bounds = class_bounds(valores, clasificacion, n_classes)
        if not bounds:
            return None

        fmt = QgsRendererRangeLabelFormat()
        fmt.setFormat("%1 – %2")
        fmt.setPrecision(2)
        fmt.setTrimTrailingZeroes(True)

        rangos = []
        n = len(bounds)
        for i, (lo, hi) in enumerate(bounds):
            simbolo = QgsSymbol.defaultSymbol(layer.geometryType())
            simbolo.setColor(ramp.color(i / (n - 1) if n > 1 else 0.0))
            rango = QgsRendererRange(lo, hi, simbolo, "")
            # labelForRange solo acepta el propio rango, no un par de floats.
            rango.setLabel(fmt.labelForRange(rango))
            rangos.append(rango)

        renderer = QgsGraduatedSymbolRenderer(field_name, rangos)
        renderer.setClassificationMethod(_classification_method(clasificacion))
        renderer.setLabelFormat(fmt)
        renderer.setSourceColorRamp(ramp.clone())
        return renderer
    except Exception:
        return None


def _apply_graduated_style(layer, field_name, n_classes=5, clasificacion="jenks"):
    """Aplica simbología graduada con el método de clasificación elegido."""
    renderer = _graduated_renderer(layer, field_name, n_classes, clasificacion)
    if renderer is None:
        return
    layer.setRenderer(renderer)
    layer.triggerRepaint()


# ─────────────────────────────────────────────────────────────────────────────
# Nivel manzano / comunidad
# ─────────────────────────────────────────────────────────────────────────────

# Nombre de la tabla dentro del GeoPackage y tipo de geometría de cada área.
_AREA_SPEC = {
    "urbana": ("manzanos",    "MultiPolygon", "Urbana"),
    "rural":  ("comunidades", "Point",        "Rural"),
}


def _geometria_municipio(municipio):
    """(QgsGeometry, nombre) del municipio, desde el GeoJSON ya empaquetado.

    Sirve de contexto para los mapas de manzano y comunidad: sin el límite
    municipal alrededor, unos cuantos polígonos sueltos quedan "al aire" y no se
    sabe dónde caen. Retorna (None, "") si no se encuentra.
    """
    from qgis.core import QgsGeometry, QgsVectorLayer

    path = GEO_FILES.get("municipio")
    if not path or not path.exists():
        return None, ""
    src = QgsVectorLayer(str(path), "tmp_municipio", "ogr")
    if not src.isValid():
        return None, ""

    code = str(municipio).zfill(6)
    campos = [f.name() for f in src.fields()]
    for feat in src.getFeatures():
        props = {name: feat[name] for name in campos}
        if _get_geo_code(props, "municipio") == code:
            # Copia explícita: la feature (y su geometría) muere con el iterador.
            return QgsGeometry(feat.geometry()), str(props.get("nombre_mun") or code)
    return None, ""


def _capa_memoria_municipio(geom, nombre):
    """Capa de una sola feature con el límite del municipio."""
    from qgis.core import (
        QgsVectorLayer, QgsFeature, QgsField, QgsFields,
    )
    from .compat import TIPO_TEXTO

    layer = QgsVectorLayer("MultiPolygon?crs=EPSG:4326", "municipio", "memory")
    fields = QgsFields()
    fields.append(QgsField("nombre_geo", TIPO_TEXTO))
    layer.dataProvider().addAttributes(fields)
    layer.updateFields()

    feat = QgsFeature(layer.fields())
    feat.setGeometry(geom)
    feat.setAttributes([nombre])
    layer.dataProvider().addFeatures([feat])
    layer.updateExtents()
    return layer


def _estilo_contexto(layer):
    """Relleno tenue y borde marcado: da referencia sin competir con los datos."""
    try:
        from qgis.core import QgsFillSymbol
        simbolo = QgsFillSymbol.createSimple({
            "color": "235,235,235,90",
            "outline_color": "90,90,90,255",
            "outline_width": "0.4",
        })
        layer.renderer().setSymbol(simbolo)
        layer.triggerRepaint()
    except Exception as exc:
        # El estilo del contexto es decorativo: la capa sirve igual sin él.
        log.aviso("No se pudo aplicar el estilo a la capa de contexto", exc)


def _capa_memoria_unidades(geoms, lookup, area):
    """Capa temporal en memoria con las geometrías WKB de un área.

    Las unidades sin dato entran con `valor_censo` nulo: así el mapa muestra el
    tejido completo del municipio (el INE reserva la ficha de las unidades con
    poca población) en vez de dejar huecos sin explicar.
    """
    from qgis.core import (
        QgsVectorLayer, QgsFeature, QgsGeometry, QgsField, QgsFields,
    )
    from .compat import TIPO_DECIMAL, TIPO_TEXTO

    tabla, wkb_type, area_lbl = _AREA_SPEC[area]
    layer = QgsVectorLayer(f"{wkb_type}?crs=EPSG:4326", tabla, "memory")

    fields = QgsFields()
    fields.append(QgsField("codigo", TIPO_TEXTO))
    fields.append(QgsField("nombre_geo", TIPO_TEXTO))
    fields.append(QgsField("area", TIPO_TEXTO))
    fields.append(QgsField("valor_censo", TIPO_DECIMAL))
    layer.dataProvider().addAttributes(fields)
    layer.updateFields()

    feats = []
    for codigo, nombre, wkb in geoms:
        geom = QgsGeometry()
        geom.fromWkb(bytes(wkb))
        if geom.isNull():
            continue
        f = QgsFeature(layer.fields())
        f.setGeometry(geom)
        valor = lookup.get(str(codigo))
        f.setAttributes([str(codigo), str(nombre or ""), area_lbl,
                         None if valor is None else float(valor)])
        feats.append(f)

    layer.dataProvider().addFeatures(feats)
    layer.updateExtents()
    return layer, tabla


def _escribir_gpkg(layer, out_path, tabla, primera):
    """Materializa una capa de memoria como tabla de un GeoPackage."""
    from qgis.core import QgsVectorFileWriter, QgsCoordinateTransformContext

    opts = QgsVectorFileWriter.SaveVectorOptions()
    opts.driverName = "GPKG"
    opts.layerName = tabla
    opts.actionOnExistingFile = (
        QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteFile if primera
        else QgsVectorFileWriter.ActionOnExistingFile.CreateOrOverwriteLayer
    )
    res = QgsVectorFileWriter.writeAsVectorFormatV3(
        layer, str(out_path), QgsCoordinateTransformContext(), opts)
    if res[0] != QgsVectorFileWriter.WriterError.NoError:
        raise RuntimeError(f"No se pudo escribir el GeoPackage: {res[1]}")


def _renderer_para_puntos(renderer):
    """Adapta un renderer graduado de polígonos a símbolos de punto.

    Las comunidades rurales son puntos y los manzanos polígonos, así que van en
    capas distintas; clonar el renderer (mismos cortes, mismos colores) es lo que
    hace comparables los dos mapas.
    """
    from qgis.core import QgsMarkerSymbol

    clon = renderer.clone()
    for i, rango in enumerate(clon.ranges()):
        color = rango.symbol().color()
        simbolo = QgsMarkerSymbol.createSimple({
            "name": "circle", "size": "2", "outline_style": "no",
        })
        simbolo.setColor(color)
        clon.updateRangeSymbol(i, simbolo)
    return clon


def _sin_borde(renderer):
    """Quita el trazo de los polígonos: a escala de manzano tapa el relleno."""
    from qgis.PyQt.QtCore import Qt
    for rango in renderer.ranges():
        capa_simbolo = rango.symbol().symbolLayer(0)
        try:
            capa_simbolo.setStrokeStyle(Qt.PenStyle.NoPen)
        except AttributeError:
            pass
    return renderer


def crear_capa_unidades(df_agregado, geoms, nombre_capa, iface=None,
                        clasificacion="jenks", municipio=None):
    """
    Crea las capas de manzanos y/o comunidades de un municipio con el indicador.

    - df_agregado: DataFrame [geo_code, geo_nombre, valor] a nivel unidad
    - geoms:       {"urbana": [(codigo, nombre, wkb), …], "rural": [...]}
    - nombre_capa: nombre base, y nombre del grupo de capas
    - municipio:   código de 6 dígitos; si se pasa, se añade el límite municipal
                   como capa de contexto debajo de los datos

    Retorna la lista de capas creadas (los datos primero, el contexto al final).
    """
    from qgis.core import QgsProject, QgsVectorLayer

    lookup = {str(r["geo_code"]): r["valor"] for _, r in df_agregado.iterrows()}
    presentes = [a for a in ("urbana", "rural") if geoms.get(a)]
    if not presentes:
        raise RuntimeError(
            "No hay geometrías para este municipio y área. Revisa la selección."
        )

    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", nombre_capa) or "capa"
    out_path = _capas_dir() / f"{safe}_{uuid.uuid4().hex[:8]}.gpkg"

    capas = []
    for i, area in enumerate(presentes):
        mem, tabla = _capa_memoria_unidades(geoms[area], lookup, area)
        _escribir_gpkg(mem, out_path, tabla, primera=(i == 0))
        etiqueta = nombre_capa if len(presentes) == 1 else f"{nombre_capa} · {tabla}"
        capa = QgsVectorLayer(f"{out_path}|layername={tabla}", etiqueta, "ogr")
        if not capa.isValid():
            raise RuntimeError(f"No se pudo cargar la capa: {out_path} ({tabla})")
        capas.append((area, capa))

    # Un solo juego de cortes para las dos áreas: se calculan sobre la capa con
    # más unidades y se reutilizan, para que los colores signifiquen lo mismo.
    principal = max(capas, key=lambda t: t[1].featureCount())[1]
    base = _graduated_renderer(principal, "valor_censo", clasificacion=clasificacion)

    for area, capa in capas:
        if base is None:
            continue
        renderer = base.clone() if area == "urbana" else _renderer_para_puntos(base)
        if area == "urbana":
            _sin_borde(renderer)
        capa.setRenderer(renderer)
        capa.triggerRepaint()

    # Contexto: el límite del municipio al que pertenecen estas unidades. Va en
    # el mismo GeoPackage para que la capa siga siendo autocontenida.
    contexto = None
    if municipio:
        geom_mun, nombre_mun = _geometria_municipio(municipio)
        if geom_mun is not None and not geom_mun.isNull():
            mem = _capa_memoria_municipio(geom_mun, nombre_mun)
            _escribir_gpkg(mem, out_path, "municipio", primera=False)
            contexto = QgsVectorLayer(f"{out_path}|layername=municipio",
                                      f"{nombre_mun} (contexto)", "ogr")
            if contexto.isValid():
                _estilo_contexto(contexto)
            else:
                contexto = None

    proyecto = QgsProject.instance()
    if len(capas) == 1 and contexto is None:
        proyecto.addMapLayer(capas[0][1])
    else:
        grupo = proyecto.layerTreeRoot().insertGroup(0, nombre_capa)
        # Orden de arriba (se dibuja encima) hacia abajo: las comunidades son
        # puntos y quedarían tapadas por los polígonos urbanos; el contexto va
        # al fondo, por debajo de todos los datos.
        ordenadas = ([c for a, c in capas if a == "rural"]
                     + [c for a, c in capas if a == "urbana"]
                     + ([contexto] if contexto is not None else []))
        for capa in ordenadas:
            proyecto.addMapLayer(capa, False)
            grupo.addLayer(capa)

    resultado = [capa for _, capa in capas] + ([contexto] if contexto else [])

    if iface:
        # Encuadre sobre el municipio completo cuando hay contexto: es el marco
        # de referencia, y siempre contiene a la mancha urbana.
        extent = None
        for capa in resultado:
            ext = capa.extent()
            if extent is None:
                extent = ext
            else:
                extent.combineExtentWith(ext)
        if extent is not None and not extent.isEmpty():
            iface.mapCanvas().setExtent(extent)
        iface.mapCanvas().refresh()

    return resultado


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
            NULL,
            QgsCategorizedSymbolRenderer,
            QgsRendererCategory,
            QgsSymbol,
            QgsStyle,
        )
        from qgis.PyQt.QtGui import QColor

        # Recopilar valores únicos del campo. Los vacíos llegan como QVariant
        # nulo (no como None): sin descartarlos aparecería una categoría "NULL".
        values = sorted({
            str(feat[field_name])
            for feat in layer.getFeatures()
            if feat[field_name] != NULL
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
    except Exception as exc:
        # Sin simbología la capa se carga igual, con el estilo por defecto.
        log.aviso("No se pudo aplicar la simbología categórica", exc)
