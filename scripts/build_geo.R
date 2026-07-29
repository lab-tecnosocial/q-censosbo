#!/usr/bin/env Rscript
# Genera las geometrías empaquetadas del plugin desde el paquete R `censosbo`.
#
#   qcensosbo/data/geo_municipios.geojson      343 municipios del CPV-2024
#   qcensosbo/data/geo_departamentos.geojson     9 departamentos
#
# ¿Por qué un script de R en un plugin de Python? Porque la fuente ES un dataset
# de R: `censosbo::geo_municipios`, que el paquete construye cruzando los límites
# de SDSN Bolivia con los códigos y nombres del INE (ver su viñeta «Mapas
# coropléticos»). Antes estos GeoJSON entraron en el commit inicial del plugin sin
# script ni procedencia, y no había forma de regenerarlos: eso es lo que este
# archivo arregla. Los datos del plugin siguen siendo los mismos que los del
# paquete, sin una copia intermedia que se desincronice.
#
# Lo que corrige respecto a la capa anterior (censosbo 1.6.0):
#   - 343 municipios en vez de 339. Los cuatro GAIOC creados desde 2016 no eran
#     huecos en el mapa: su territorio se dibujaba dentro del municipio madre, así
#     que sus datos salían MAL ATRIBUIDOS sin que se notara (7.599 km²).
#   - Sin las 1.126 franjas blancas espurias (461 km²) que la simplificación
#     anterior dejaba entre municipios vecinos.
#   - `capital` y `superficie_km2` como columnas nuevas.
#
# Uso:
#   Rscript scripts/build_geo.R
#
# Requiere R con los paquetes `censosbo` (>= 1.6.0) y `sf`.

suppressMessages({
  library(censosbo)
  library(sf)
})

# Raíz del repo: la del --file si se invoca con Rscript, o el directorio actual.
argumentos <- commandArgs(trailingOnly = FALSE)
arg_file <- grep("^--file=", argumentos, value = TRUE)
raiz <- if (length(arg_file)) {
  normalizePath(file.path(dirname(sub("^--file=", "", arg_file[1])), ".."))
} else {
  getwd()
}
destino <- file.path(raiz, "qcensosbo", "data")
if (!dir.exists(destino)) {
  stop(sprintf("No encuentro %s. Ejecuta el script desde el repo del plugin.", destino))
}

version_minima <- "1.6.0"
if (packageVersion("censosbo") < version_minima) {
  stop(sprintf(
    "Se necesita censosbo >= %s (instalado: %s). La cartografia de 343 municipios llego en 1.6.0.",
    version_minima, packageVersion("censosbo")))
}

# 6 decimales son ~10 cm: de sobra para limites municipales, y evita que el ZIP
# del plugin cargue con precision que nadie usa.
opciones <- c("COORDINATE_PRECISION=6", "RFC7946=YES")

escribir <- function(capa, nombre, esperadas) {
  if (nrow(capa) != esperadas) {
    stop(sprintf("%s: se esperaban %d filas y hay %d.", nombre, esperadas, nrow(capa)))
  }
  if (is.na(st_crs(capa)$epsg) || st_crs(capa)$epsg != 4326) {
    capa <- st_transform(capa, 4326)
  }
  ruta <- file.path(destino, nombre)
  antes <- if (file.exists(ruta)) file.size(ruta) else NA_integer_
  suppressWarnings(st_write(capa, ruta, driver = "GeoJSON", delete_dsn = TRUE,
                            layer_options = opciones, quiet = TRUE))
  ahora <- file.size(ruta)
  cat(sprintf("  %-28s %3d features  %6.0f KB%s\n", nombre, nrow(capa), ahora / 1024,
              if (is.na(antes)) "" else sprintf("  (antes %.0f KB)", antes / 1024)))
  invisible(ruta)
}

cat(sprintf("Cartografia desde censosbo %s\n", packageVersion("censosbo")))
escribir(censosbo::geo_municipios,    "geo_municipios.geojson",    343)
escribir(censosbo::geo_departamentos, "geo_departamentos.geojson",   9)

# El plugin arma el codigo de municipio como idep+iprov+imun; si la fuente dejara
# de traer esas tres columnas, el join fallaria en silencio y el mapa saldria vacio.
faltan <- setdiff(c("idep", "iprov", "imun", "nombre_mun"),
                  names(censosbo::geo_municipios))
if (length(faltan)) {
  stop(sprintf("Faltan columnas que el plugin necesita: %s",
               paste(faltan, collapse = ", ")))
}
cat("  ok: idep/iprov/imun/nombre_mun presentes\n")
