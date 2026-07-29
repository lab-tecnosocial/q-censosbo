#!/usr/bin/env Rscript
# Genera `qcensosbo/data/docs_variables.csv`: la documentación conceptual del INE
# para las variables del censo, que el panel muestra en el tooltip de cada una.
#
# La fuente es `censosbo::codebook_docs_meta` (445 variables de los cinco censos,
# extraídas de los diccionarios DDI del catálogo ANDA del INE). **No viaja a los
# GitHub Releases**, solo existe como dataset del paquete R, así que igual que con
# el catálogo de fichas hay que empaquetarla: el plugin no puede descargarla.
#
# De las diez columnas del dataset se empaquetan seis. Las que se dejan fuera y por
# qué:
#   - `instruccion` — las instrucciones al empadronador. Es el campo más pesado
#     (385 caracteres de media, hasta 4.321) y va dirigido a quien levanta el censo,
#     no a quien lo analiza. Duplicaría el peso del archivo.
#   - `notas` e `informante` — marginales para decidir qué variable usar.
#
# La clave es (anio, tabla, variable): sin el año hay 22 colisiones, porque la misma
# variable aparece en varios censos con definiciones distintas.
#
# Uso:
#   Rscript scripts/build_docs_vars.R
#
# Requiere R con el paquete `censosbo` (>= 1.5.0).

suppressMessages(library(censosbo))

argumentos <- commandArgs(trailingOnly = FALSE)
arg_file <- grep("^--file=", argumentos, value = TRUE)
raiz <- if (length(arg_file)) {
  normalizePath(file.path(dirname(sub("^--file=", "", arg_file[1])), ".."))
} else {
  getwd()
}
destino <- file.path(raiz, "qcensosbo", "data", "docs_variables.csv")
if (!dir.exists(dirname(destino))) {
  stop(sprintf("No encuentro %s. Ejecuta el script desde el repo del plugin.",
               dirname(destino)))
}

COLUMNAS <- c("anio", "tabla", "variable", "definicion", "pregunta_literal",
              "universo_literal", "regla_derivacion")

docs <- censosbo::codebook_docs_meta
faltan <- setdiff(COLUMNAS, names(docs))
if (length(faltan)) {
  stop(sprintf("codebook_docs_meta no trae: %s", paste(faltan, collapse = ", ")))
}

salida <- docs[, COLUMNAS]
# Los textos del DDI traen saltos de línea y espacios repetidos; el tooltip los
# reflowa igual, y así el CSV no se llena de comillas multilínea.
for (col in c("definicion", "pregunta_literal", "universo_literal",
              "regla_derivacion")) {
  salida[[col]] <- gsub("\\s+", " ", trimws(ifelse(is.na(salida[[col]]), "",
                                                   salida[[col]])))
}
salida <- salida[order(salida$anio, salida$tabla, salida$variable), ]

dup <- sum(duplicated(salida[, c("anio", "tabla", "variable")]))
if (dup > 0) {
  stop(sprintf("Hay %d filas duplicadas por (anio, tabla, variable).", dup))
}

antes <- if (file.exists(destino)) file.size(destino) else NA_integer_
write.csv(salida, destino, row.names = FALSE, na = "")

cat(sprintf("docs_variables.csv: %d filas, %.0f KB%s\n",
            nrow(salida), file.size(destino) / 1024,
            if (is.na(antes)) "" else sprintf(" (antes %.0f KB)", antes / 1024)))
cat(sprintf("  con definicion: %d | con pregunta literal: %d | con regla: %d\n",
            sum(nzchar(salida$definicion)),
            sum(nzchar(salida$pregunta_literal)),
            sum(nzchar(salida$regla_derivacion))))
cat(sprintf("  censos: %s\n", paste(sort(unique(salida$anio)), collapse = ", ")))
