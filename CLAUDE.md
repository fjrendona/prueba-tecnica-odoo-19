# Instrucciones de Desarrollo - Prueba Técnica Odoo 19

Eres un AI Assistant experto en desarrollo para Odoo 19 Community. Estás desarrollando una **prueba técnica de desarrollador senior**: el código será evaluado por su estructura, calidad, uso idiomático de Odoo 19 y por la justificación de las decisiones de diseño.

## 0. El proyecto

Un único módulo Odoo 19 que:

1. **Importa ventas desde Excel** mediante un wizard (`TransientModel` con campo `Binary`).
2. **Valida** cada fila y crea registros en un **modelo personalizado** de ventas importadas.
3. Ofrece un **dashboard** de análisis comercial: total vendido en el mes, número de ventas, ventas por vendedor / cliente / producto / estado, gráfico (barras o circular) y vista pivot.
4. **Refleja en tiempo real** en la vista lista nativa los registros nuevos (de una importación, de otro usuario o de una integración), sin recarga manual y sin polling.

Nombre técnico del módulo: `sales_import_analytics`, en la raíz del repo. Si se cambia, actualizar esta línea.

### Excel de entrada

Columnas, en este orden: `Fecha | Cliente | Vendedor | Producto | Cantidad | Valor Unitario | Valor Total | Estado`
Ejemplo: `2026-06-01 | Cliente A | Juan Pérez | Producto 1 | 2 | 50000 | 100000 | Confirmada`

### Validaciones obligatorias

- Obligatorios: Fecha, Cliente, Vendedor, Producto y Estado.
- `Cantidad > 0` y `Valor Total > 0`.
- Se aplican **en el modelo** (`@api.constrains` / `models.Constraint`), no solo en el wizard, para que cualquier vía de creación (UI, import, RPC) las cumpla.
- El wizard **no aborta en la primera fila mala**: recopila los errores por fila (número de fila, columna y motivo) y los muestra claros al usuario. Si hay filas inválidas no se crea nada, de modo que la importación es todo o nada, salvo que la spec decida otra cosa.
- También se manejan: archivo que no es Excel, archivo vacío, cabeceras faltantes o en otro orden, celdas con tipo incorrecto (fecha como texto, números con separadores) y filas completamente vacías, que se ignoran.

### Requerimientos técnicos

- Vistas: lista, formulario, búsqueda (filtros y `group_by` por vendedor/cliente/producto/estado/fecha), graph y pivot.
- Menú de acceso al dashboard y al wizard.
- `security/ir.model.access.csv` para el modelo y el wizard.
- Entregables: `README.md` (instalación, uso y **respuestas a las 4 preguntas de tiempo real**), **Excel de ejemplo** con datos válidos y otro con datos inválidos, y la sección de uso de herramientas agénticas (sección 9).

### Tiempo real: requisitos que se evalúan

| Requisito | Qué implica |
|---|---|
| Sin polling | El servidor avisa por el **bus de Odoo** (websocket). No usar `setInterval` ni recargas por temporizador. |
| Control de ráfagas | Ante N creaciones seguidas, recargar **una vez**, y seguir recargando periódicamente si el flujo es sostenido. Usar **throttle con trailing edge** (o debounce con `maxWait`), no un debounce simple, que con eventos continuos no dispara nunca (inanición). Esto se justifica en el README. |
| Sin duplicar UI | Extender la **vista lista nativa** con `js_class` (controller que hereda `ListController` y se registra en `registry.category("views")`), reutilizando su renderer y su modelo (`this.model.load()` / `this.model.root.load()`). Nada de componente de lista propio. |
| Limpieza | Suscribirse en `setup()` y desuscribirse en `onWillUnmount`/`onWillDestroy` (`bus_service.unsubscribe`, cancelar el temporizador del throttle). Protegerse contra la condición de carrera: un evento o un timer que llegue durante el desmontaje no debe llamar a `load()` sobre un componente destruido (flag `isDestroyed` / `status(this) === "destroyed"`). |
| Envío servidor | Emitir la notificación tras crear registros (`create` con `@api.model_create_multi`), **una por lote**, no una por registro. El bus encola en `precommit` y el mensaje sale al hacer commit, así que nunca llega un aviso de algo que luego hizo rollback. |

Datos de la API verificados en el código fuente de Odoo 19:
- Servidor (decisión R1 del plan): `env.ref("sales_import_analytics.group_user")._bus_send("sales_import_analytics/new_records", {})`. `res.groups` hereda `bus.listener.mixin` y `ir.websocket._build_bus_channel_list` suscribe automáticamente a cada sesión a **todos los grupos del usuario**: sin canal `str`, sin override y sin `addChannel`. El aviso sale al hacer commit (`cr.precommit`).
- Cliente: servicio `bus_service` con `subscribe(type, cb)` / `unsubscribe(type, cb)` / `addChannel(channel)` (`addons/bus/static/src/services/bus_service.js`).
- `openpyxl` 3.1.2 y `xlrd` 2.0.1 ya vienen en la imagen: no hace falta añadirlos a `requirements.txt`.

### Decisiones de diseño (aprobadas por el usuario el 2026-09-24; no reabrir sin preguntar)

| Tema | Decisión |
|---|---|
| Cliente | `Many2one res.partner`, buscar o crear. |
| Producto | `Many2one product.product`: busca por referencia interna y luego por nombre; si no existe, lo crea como consumible sin tocar el precio. Depende de `product`. |
| Vendedor | Modelo propio `sales.import.salesperson` (`name` y `user_id` opcional, que se vincula si existe un usuario con ese nombre), buscar o crear. **Nunca crear `res.users`.** |
| Búsqueda de referencias | Texto normalizado (sin espacios sobrantes, sin distinguir mayúsculas). Los existentes se precargan en un diccionario, sin `search` por fila. Al final, resumen de lo creado ("3 clientes y 2 productos nuevos"). |
| Estado | `Selection`: `draft` / `confirmed` / `cancelled`. Mapeo tolerante mediante diccionario (sin distinguir mayúsculas ni tildes; admite "Confirmada", "confirmado", "Confirmed"…). Un valor desconocido es **error de fila**, nunca un valor por defecto. |
| Valor Total | Se guarda el valor importado (`Monetary`, moneda de la compañía, no calculado) y se valida contra cantidad × unitario con la tolerancia de redondeo de la moneda. Si no coincide, error de fila. |
| Duplicados | Por archivo, no por fila. Modelo `sales.import.batch` con nombre de archivo, huella SHA-256 **del contenido** (filas leídas y normalizadas, no los bytes: Excel cambia los bytes al reguardar), usuario, fecha y conteos; cada venta enlaza a su lote. Si la huella se repite, se bloquea salvo que se marque la opción explícita de forzar. Borrar un lote borra sus ventas. |
| Errores de importación | Todo o nada: se validan todas las filas y se reportan todos los errores (fila y columna). Con un solo error no se crea nada. |
| Total del mes | Graph y pivot agrupan por defecto por **mes de la fecha de venta**, no por el mes actual. El gráfico apila por **estado** (el segmento Confirmada es el total vendido) y el tablero abre **sin filtro** para ver los tres estados; el filtro "Confirmadas" queda a un clic. *(Revisado por el usuario el 2026-09-24: antes era filtro por defecto "Confirmadas", que ocultaba Borrador y Cancelada.)* |
| Dashboard | Base: acción nativa graph (por defecto), pivot y lista, con medidas total y número de ventas, y agrupaciones predefinidas por vendedor, cliente, producto y estado. **Opcional, solo si sobra tiempo:** acción cliente OWL con tarjetas de KPIs que embebe las vistas nativas mediante `View` (`@web/views/view`). |
| Ramas | Una sola rama para toda la prueba: `feat/fjrendona-sales-import-analytics`, con commits por incremento. |

## 1. Entorno

- Código fuente de Odoo 19 Community (clon de GitHub, rama `19.0`): `/home/franciscorendon/odoo-19/odoo` (`ODOO_SRC` en `.env`). Es la **fuente de verdad** de la API: ante la duda, grep dirigido ahí (p. ej. `addons/bus/`, `addons/web/static/src/views/list/`). **Nunca modificarlo.**
- Docker: `docker compose up -d`, que aplica el override de desarrollo. Contenedores `odoo_19` y `odoo19_db`. Odoo en `http://localhost:8070`  (usuario `admin` / `admin`). Postgres en `localhost:5433`.
- Instalar o actualizar el módulo:
  ```bash
  docker exec -it odoo_19 python3 /opt/odoo/odoo-bin -d odoo19_db \
    --db_host=db --db_user=odoo --db_password=odoo \
    --addons-path=/opt/odoo/addons,/mnt/extra-addons \
    -u sales_import_analytics --stop-after-init --http-port=8099
  docker restart odoo_19
  ```
  Usar `-i` en la primera instalación. Tras crear la carpeta del módulo por primera vez hay que reiniciar `odoo_19` para que `/mnt/extra-addons` entre en el `addons_path`.
- Tests: mismo comando con `--test-enable --test-tags=/sales_import_analytics`.
- Logs: `logs/odoo.log`.

## 2. Estándares Odoo 19 (OBLIGATORIO)

- Antes de escribir código Odoo, lee `.claude/skills/odoo-19/references/api-highlights.md` y la guía de `references/` que toque (model, view, owl, security, testing…).
- Vistas: `<list>` (no `<tree>`), atributos directos `invisible="..."`/`readonly="..."` (no `attrs`/`states`), kanban `t-name="card"`, `t-out` (no `t-esc`).
- Restricciones SQL con `models.Constraint` / `models.Index` (`_sql_constraints` se ignora en 19).
- `_read_group()` / `formatted_read_group()`, nunca `read_group()`. `self.env.cr` / `self.env.uid` / `self.env.context`, nunca `self._cr` / `self._uid` / `self._context`.
- `@api.model_create_multi` en `create`. `@api.returns` no existe en 19.
- `_name` explícito en todo modelo nuevo. Grupos con `privilege_id` si se crean.
- Campos monetarios con `fields.Monetary` + `currency_id`. Agregación con `aggregator=`.
- JS: módulos ES (`/** @odoo-module **/` no es necesario en 19), OWL 2, servicios con `useService`. Assets declarados en `web.assets_backend` del manifest.
- Las BD nuevas no tienen datos demo: los tests crean sus propios registros.
- Todos los identificadores (clases, métodos, variables, archivos, XML IDs) en **inglés**. Los `string=` y textos de UI en español. Textos de UI y mensajes de error traducibles con `_()` / `self.env._()`.
- Orden de `data` en el manifest: `security/*.xml` → `security/ir.model.access.csv` → `wizard/*.xml` → `views/*.xml` → menús al final.
- Versión del manifest `19.0.x.y.z`. Se sube en cada commit que toque el módulo, porque Odoo no recarga vistas ni datos de un módulo cuya versión no cambia. Tras actualizar, reiniciar el servicio.
- Seguir las guías oficiales de Odoo 19: [Coding guidelines](https://www.odoo.com/documentation/19.0/contributing/development/coding_guidelines.html) (estructura de módulo, nombres de archivos y XML IDs, orden de atributos y métodos en la clase) y [Security pitfalls](https://www.odoo.com/documentation/19.0/developer/reference/backend/security.html). Si la skill `odoo-19` y el código fuente de `ODOO_SRC` difieren, manda el código fuente.

## 2.1 Clean Code, SOLID, rendimiento y seguridad (OBLIGATORIO)

El código debe ser **limpio, modular, optimizado y seguro**. Estos principios se aplican **dentro de las convenciones de Odoo**, no contra ellas: nada de capas, interfaces o patrones de otros frameworks (repositorios, DTOs, inyección de dependencias propia) que Odoo ya resuelve con el ORM, la herencia y el registry. Si un principio empuja a añadir una abstracción sin un segundo uso real, gana `ponytail`.

**Clean Code**
- Nombres que revelan intención: `_parse_sale_rows()`, `_notify_new_sales()`, no `process()` ni `do_it()`. Sin abreviaturas crípticas.
- Funciones cortas con una sola responsabilidad y un solo nivel de abstracción. Si un método necesita comentarios para separar bloques ("# leer", "# validar", "# crear"), son tres métodos.
- Sin números ni cadenas mágicas: cabeceras del Excel, tipo de notificación del bus y tiempos del throttle como constantes con nombre, en un único sitio.
- Sin código muerto, `print`, `console.log`, `pdb` ni código comentado. Logs con `_logger` y el nivel adecuado.
- Comentarios y docstrings para el **por qué** (una restricción de Odoo, una decisión de diseño), no para repetir el qué.
- Guard clauses y retornos tempranos en lugar de `if` anidados.
- DRY con criterio: extraer solo cuando la duplicación es real (tres usos), no preventivamente.
- Estilo: PEP 8 con las reglas del `ruff.toml` de `ODOO_SRC`. En JS, el estilo de `addons/web` de Odoo 19. XML indentado con 4 espacios.

**SOLID aplicado a Odoo**
- **S (responsabilidad única):**
  - El parser del Excel (bytes → filas + errores) no conoce el ORM.
  - El wizard orquesta: lee, delega la validación y crea.
  - El modelo garantiza sus invariantes con constraints.
  - El controller JS solo reacciona a eventos y recarga; no renderiza.
- **O (abierto/cerrado):** extender con `_inherit`, `xpath`, `js_class` y `patch()`, nunca copiando ni modificando código de Odoo. Los mapeos (columna → campo, texto de estado → valor de la selección) van en estructuras de datos, no en cadenas de `if`/`elif`.
- **L (sustitución de Liskov):** al sobrescribir `create`, `write`, `setup()` u `onWillUnmount`, llamar siempre a `super()` y respetar la firma y el tipo de retorno. Un override no debe romper a quien usa el método original.
- **I (segregación de interfaces):** métodos públicos mínimos. Lo interno lleva prefijo `_`, que tampoco es invocable por RPC; si algo público no debe exponerse por RPC, `@api.private`.
- **D (inversión de dependencias):** depender de los servicios y del ORM de Odoo (`useService("bus_service")`, `self.env[...]`), no de instancias concretas ni de variables globales. El manifest declara solo las dependencias que se usan.

**Rendimiento**
- Creación en lote: un solo `create(vals_list)` por importación, nunca `create` en un bucle.
- Sin N+1: nada de `search` ni `browse` dentro de bucles. Resolver referencias (si hay `Many2one`) con una consulta previa y un diccionario.
- `openpyxl.load_workbook(..., read_only=True, data_only=True)` para no cargar estilos ni fórmulas.
- Campos por los que se agrupa o filtra en el dashboard (fecha, vendedor, cliente, producto, estado) con `index=True`. Totales como campos almacenados para que graph y pivot agreguen en SQL.
- Agregados con `_read_group()`, nunca sumando en Python sobre `search()`.
- Tiempo real: una notificación de bus por lote. En el cliente, throttle y un solo `load()` por ventana.

**Seguridad**
- Todo modelo y wizard con su línea en `ir.model.access.csv`. Nada de `sudo()` salvo necesidad justificada en un comentario, y nunca para saltarse las ACL del usuario que importa, con una única excepción:
  - **Excepción documentada a la regla de `sudo()`** (aprobada 2026-09-24): se permite `sudo()`
    exclusivamente en métodos privados dedicados a crear los registros maestros faltantes durante la
    importación (`res.partner`, `product.product`), bajo estas condiciones: (a) solo `create`, nunca
    `write`/`unlink` sobre existentes; (b) campos limitados a una lista blanca mínima; (c) verificación
    previa de que el usuario pertenece a `sales_import_analytics.group_manager`; (d) cada registro
    creado queda contado en el lote de importación. Cualquier otro uso de `sudo()` sigue prohibido.
- Sin SQL crudo con interpolación de cadenas: ORM o la clase `SQL` con parámetros.
- Validar el archivo subido: extensión y contenido real (que `openpyxl` lo pueda abrir), tamaño máximo razonable, límite de filas y hojas vacías. Nunca ejecutar ni evaluar el contenido de las celdas (sin `eval` ni `safe_eval` sobre datos del usuario).
- Errores al usuario con `UserError` / `ValidationError` y mensajes claros, sin volcar trazas ni detalles internos. Las excepciones inesperadas se registran en el log.
- XSS: en QWeb usar `t-out`, que escapa por defecto; nunca envolver datos del Excel en `Markup`. En OWL, nada de `innerHTML` ni `markup()` con datos del usuario.
- Bus: canal ligado a un registro (usuario o compañía) mediante `_bus_send`, o un canal `str` no adivinable. El payload lleva el mínimo imprescindible (p. ej. solo el aviso de "hay nuevos"), nunca datos de registros que el receptor quizá no pueda leer: la lista se recarga con las ACL del propio usuario.
- Controllers (si los hay): `auth` y `csrf` explícitos, con validación de entrada.

## 3. Calidad que se evalúa

- **Estructura:** `models/`, `wizard/`, `views/`, `security/`, `static/src/`, `tests/`, `data/` (si aplica). Un modelo por archivo.
- **Lectura del Excel aislada** en un método o clase pura (bytes → filas normalizadas + errores), testeable sin UI.
- **Tests obligatorios:** parseo válido, cada validación, archivo corrupto o vacío, cabeceras erróneas, creación en lote y emisión de **una** notificación de bus por lote. Si es viable, un tour o test JS de la lista en tiempo real.
- **UX:** errores legibles (fila + motivo), notificación de éxito con el número de registros creados y acción de retorno a la lista.
- **Simplicidad (skill `ponytail`):** la solución nativa más corta que cumple. Nada de abstracciones sin un segundo uso.

## 4. Flujo de trabajo por tarea (OBLIGATORIO)

1. Lee `.project-context-cache.md` (contexto y decisiones previas).
2. **Spec Kit antes de escribir código:** `/speckit-specify` → `/speckit-clarify` (si hay decisiones abiertas) → `/speckit-plan` → `/speckit-tasks` → `/speckit-analyze` → `/speckit-implement`. La constitución está en `.specify/memory/constitution.md` y las specs en `specs/NNN-<feature>/`. **Ambas carpetas están fuera de git: no commitearlas nunca.**
3. HU o requisito ambiguo: **preguntar antes de codificar**, nunca asumir.
4. Implementar, instalar o actualizar el módulo en Docker, pasar los tests y verificar en el navegador.
5. Actualizar `.project-context-cache.md` (no versionado): rama, tarea, archivos y decisiones.

## 5. Ramas y commits

- Una sola rama para toda la prueba: `feat/fjrendona-sales-import-analytics` (desde `main`), con un commit por incremento. Spec Kit **no** crea ramas.
- Mensaje de commit contrastado con el diff: si dice "se agrega X", el diff contiene X.
- Al resolver conflictos, leer ambos lados línea por línea. Nunca aceptar "ours"/"theirs" completo sin revisar; tras resolver, `git diff` para confirmar que no se pierde funcionalidad.

## 6. Skills

Skills locales en `.claude/skills/`. Carga solo las que encajen con la tarea: primero lee el frontmatter `description:` y el cuerpo solo si aplica.

| Skill | Cuándo | Instalar si no existe |
|---|---|---|
| `odoo-19` | SIEMPRE que se toque código Odoo | `npx skills add https://github.com/unclecatvn/agent-skills --skill odoo-19` |
| `ponytail` | SIEMPRE al escribir, corregir o revisar código | `npx skills add https://github.com/dietrichgebert/ponytail --skill ponytail` |
| `caveman` | SIEMPRE en las respuestas (sección 7) | `npx skills add https://github.com/juliusbrussee/caveman --skill caveman` |
| `speckit-*` | SIEMPRE ante una HU o tarea nueva (sección 4) | `uv tool install specify-cli --from git+https://github.com/github/spec-kit.git && specify init --here --integration claude --script sh --non-interactive --force --ignore-agent-tools` |

`.claude/skills/` se versiona porque es un entregable de la prueba (configuración agéntica); `skills-lock.json` registra el origen de `odoo-19`, `ponytail` y `caveman`.

## 7. Modo Cavernícola (respuestas en chat)

- Cero cortesías y cero explicaciones redundantes. El "por qué" solo si es necesario o se pide.
- Texto libre ultra-conciso. Código, rutas y comandos completos y exactos.
- **No aplica a los entregables:** `README.md`, docstrings y mensajes de error de UI se escriben completos y en prosa clara, porque los lee el evaluador.

## 8. Archivos ignorados (eficiencia)

- No leer ni buscar en: `__pycache__/`, `*.pyc`, `venv/`, `.venv/`, `.git/`, `filestore/`, `sessions/`, `*.swp`, `.vscode/`, `.idea/`.
- `logs/` y `*.log`: solo para diagnosticar un error concreto (con `grep`/`tail`, nunca completos).
- `*.xlsx`: no abrir con Read. Generarlos o inspeccionarlos con `openpyxl` desde un script.
- En `ODOO_SRC`: solo grep o find dirigidos a un módulo o archivo concreto, nunca recorridos completos.

## 9. Entregable de herramientas agénticas

La prueba pide documentar el uso de Claude Code. Mantener en el `README.md` una sección que explique:
- La configuración: este `CLAUDE.md`, las skills (`odoo-19`, `ponytail`, `caveman`, Spec Kit) y su propósito.
- El flujo agéntico (Spec Kit: specify → clarify → plan → tasks → analyze → implement).
- Las decisiones tomadas por el agente y cómo se supervisaron o corrigieron. Registrarlas durante el desarrollo en `.project-context-cache.md` para poder resumirlas al final.
