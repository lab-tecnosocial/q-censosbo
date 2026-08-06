# Instalación

## Requisitos

- **QGIS 3.28** o superior, **incluido QGIS 4**. El plugin funciona en las dos ramas: se ha
  probado en 3.28, 3.44 y 4.2.
- **Conexión a internet** (las consultas se hacen sobre los datos en GitHub).
- **DuckDB**: el motor de consulta se instala solo, automáticamente, la primera vez que abres
  el panel (puede tardar unos segundos esa primera vez).

---

## Instalar desde QGIS

Q-CensosBo está en el
[repositorio oficial de complementos de QGIS](https://plugins.qgis.org/plugins/qcensosbo/), así
que se instala sin descargar nada a mano:

1. En QGIS: **Complementos → Administrar e instalar complementos…**
2. En la pestaña **Todos**, escribe `censo` en el buscador.
3. Elige **Q-CensosBo** y pulsa **Instalar complemento**.

!!! tip "Actualizaciones automáticas"
    Instalado por esta vía, QGIS te avisa cuando hay una versión nueva y la actualiza con un
    clic. Si quieres que revise solo, activa **Complementos → Administrar e instalar
    complementos… → Configuración → Buscar actualizaciones al arrancar QGIS**.

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
