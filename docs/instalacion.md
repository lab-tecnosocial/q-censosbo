# Instalación

## Requisitos

- **QGIS 3.28** o superior.
- **Conexión a internet** (las consultas se hacen sobre los datos en GitHub).
- **DuckDB**: el motor de consulta se instala solo, automáticamente, la primera vez que abres
  el panel (puede tardar unos segundos esa primera vez).

---

## Método A — Instalar desde ZIP

1. [Descarga el ZIP](https://lab-tecnosocial.github.io/q-censosbo/qcensosbo.zip).
2. En QGIS: **Complementos → Administrar e instalar complementos… → Instalar a partir de ZIP**.
3. Selecciona el archivo `qcensosbo.zip` descargado e **Instalar**.

---
## Método B — Repositorio de complementos

Permite instalar y **actualizar desde el propio QGIS**.

1. En QGIS: **Complementos → Administrar e instalar complementos… → Configuración**.
2. En **Repositorios de complementos**, pulsa **Añadir** y pega esta URL:

    ```
    https://lab-tecnosocial.github.io/q-censosbo/plugins.xml
    ```

3. Acepta. Vuelve a la pestaña **Todos** o **No instalados**, busca **Q-CensosBo** e
   **Instala el complemento**.
4. Cuando publiquemos una versión nueva, QGIS te avisará para actualizar.

---

## Abrir el plugin

Tras instalarlo, activa **Q-CensosBo** en la lista de complementos. Aparecerá un ícono en la
barra de herramientas y una entrada en el menú **Complementos**; al pulsarlo se abre el panel
lateral a la derecha del mapa.

!!! tip "Para desarrollo"
    Si trabajas sobre el código, puedes enlazar la carpeta del plugin a tu perfil de QGIS:

    ```bash
    ln -s /ruta/al/repo/q-censosbo/qcensosbo \
      "$HOME/Library/Application Support/QGIS/QGIS3/profiles/default/python/plugins/qcensosbo"
    ```

    (en Linux la ruta es `~/.local/share/QGIS/QGIS3/...`). Usa el complemento
    **Plugin Reloader** para recargar tras cada cambio.
