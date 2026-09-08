# Migración Erwin → Data Model Hub — guía de scripts

**Actualizado:** 2026-09-08 (doc 75: proyectos independientes) · Aplica a exports **"Save As XML" de Erwin 10.x**
(formato `<erwin xmlns="http://www.erwin.com/dm">`, probado con archivos de
50 MB y de **1.8 GB** — el parser es streaming: ~38 s y ~0.4 GB de RAM por
GB de XML). Índice general de TODOS los scripts: `scripts/README.md`.

**Multi-archivo (doc 32b, owner 2026-07-24):** un modelo Erwin llega partido
en ~15 archivos → el kit hace **MERGE incremental**: la identidad
cross-archivo es la clave natural (`schema.nombre`); lo repetido se ADOPTA
(y el uso nuevo se suma), los conflictos de versión los decide el **score de
uso** (2×relaciones + canvases + vistas), la misma FK en dos archivos no se
duplica, y folders/canvases homónimos del mismo proyecto se fusionan. Todos
los archivos de una familia cargan al MISMO proyecto (`--project` explícito,
obligatorio si ya existe). Cada carga deja un reporte JSON de decisiones en
`migration-reports/`.

**Proyectos INDEPENDIENTES (doc 75, owner 2026-09-07):** cada proyecto es un
universo aparte — sus tablas, esquemas, relaciones, vistas, canvases,
**estándares** (glosario, dominios, defs UDP, naming, reglas DDL) y
**versiones**. En el kit eso significa: (1) el proyecto se resuelve PRIMERO
(por el **manifiesto** `projects.json` de la carpeta o por el nombre del
archivo) y todo doc nace con `projectId`; (2) los ids de plataforma son
`uuid5("<projectId>|<Long_Id de Erwin>")` — el mismo objeto Erwin cargado en
dos proyectos tiene ids distintos; (3) la adopción por clave natural, la
unicidad del nombre físico y el prefetch se acotan al proyecto; (4) los
estándares de un proyecto con varios archivos son la **unión distinta**
(el primero gana; discrepancias → `glossary_conflicts`/`domain_conflicts` del
reporte); (5) `seed_ddl_export_rules` y `mark_base_version` corren por
proyecto. No hay backfill: la BD se reemplaza con el one-shot.

Todos los scripts viven en `scripts/erwin_migration/` (más los auxiliares en
`scripts/`). **Son agnósticos al archivo**: no hay nombres DDV, esquemas ni
rutas hardcodeadas — cualquier `.xml` de Erwin que te entreguen sirve. La
única excepción deliberada es `fix_particiones_ddv_20260717.py` (ver §7).

Validación de agnosticismo (2026-07-15): además del DDV real, se corrieron
parser, extractores, quality gate y migrate dry-run contra un XML sintético
de otro dominio (retail, esquemas `acme_*`, tabla sin Hive_Database) — todo
resolvió por estructura, no por nombres.

---

## 0. Mapa rápido: ¿cuál uso?

| Quiero… | Script | ¿Toca la BD? |
|---|---|---|
| Auditar un XML antes de cargarlo | `quality` | No |
| Cruzar XML(s) contra la BD viva y entre sí (solapes, score, estándares, estimación) | `crosscheck` | Solo lee |
| Sacar glosario/dominios/UDP a archivos | `extract_standards` | No |
| Sacar tablas/columnas/vistas/relaciones/canvases a archivos | `extract_model` | No |
| Vaciar la BD ANTES de una carga limpia (DROPea todas las tablas del schema `dmh`, incluidos usuarios/roles/auditoría; el one-shot vuelve a crear `admin`) | `reset_for_migration` | Sí (con `--apply`) |
| Cargar el modelo REAL a la plataforma | `migrate` | Sí (con `--apply`) |
| Ordenar los canvases después de cargar | `arrange_all` | Sí |
| Validar consistencia después de cargar | `audit_data_consistency` | Solo lee |
| Crear el usuario `admin` (BD nueva, para poder entrar) | `create_admin` | Sí (upsert) |
| Sembrar el ruleset base del DDL Export (8 reglas + lookups) | `seed_ddl_export_rules` | Sí (con `--apply`) |
| Marcar la versión base `v1` al cerrar la carga (sin él la web bloquea Model) | `mark_base_version` | Sí (con `--apply`) |
| Re-aplicar las 3 correcciones de partición del owner (solo XML DDV actual) | `fix_particiones_ddv_20260717` | Sí (con `--apply`) |
| Volver a la VERSIÓN BASE de un proyecto (deshace y borra toda versión posterior a su v1) | `reset_to_base_version` | Sí (con `--apply`) |
| Orquestar TODO lo anterior por carpeta (one-shot destructivo) o sumar un XML (append) | `run_migration` | Sí (con `--apply`) |

**Flujo recomendado: el orquestador `run_migration` (doc 54 + doc 75).**
El one-shot por carpeta es DESTRUCTIVO (dropea el schema `dmh` completo) y
encadena, por proyecto del manifiesto: `quality` (gate + glosario cruzado
entre los archivos del proyecto) → reset → `create_admin` → `migrate` archivo
por archivo → `audit_data_consistency` → `seed_ddl_export_rules --project` →
`arrange_all --project` → `mark_base_version` (v1 de cada proyecto):
```
.venv/bin/python -m scripts.run_migration --folder ../folder_data                  # plan (dry-run)
.venv/bin/python -m scripts.run_migration --folder ../folder_data --apply --force  # ejecuta
.venv/bin/python -m scripts.run_migration --append "ruta/otro.xml" [--project "…"] --apply   # suma UN xml
```
`--force` sigue haciendo falta para `folder_data/` (E-REL-BROKEN ×2 en UDV
Físico: el gate lo explica al final). `--manifest PATH` apunta a otro
manifiesto (default `<carpeta>/projects.json`; en `--append` sólo si se pasa).

**Manifiesto de proyectos (`projects.json`, doc 75 D9).** Decide a qué
proyecto va cada archivo ANTES de tocar la BD:
```json
{"projects": [
  {"name": "Modelo DDV", "files": ["Modelo de Datos DDV_FISICO/*.xml"],
   "description": "Modelo de Datos DDV físico (CPYBCA, Otros y los demás archivos de la carpeta)"}
]}
```
- `files` son patrones glob relativos a la carpeta (recursivos con `**`);
  varios archivos bajo un mismo `name` = UN proyecto multi-archivo (merge
  incremental + unión distinta de estándares).
- **Regla general** para lo que ningún patrón matchea: un archivo = un
  proyecto con el **nombre del archivo** sin extensión (`UDV INT FISICO.xml`
  → «UDV INT FISICO»).
- Validación estricta (`ManifestError`, aborta antes del wipe): patrón sin
  match, un archivo en dos entradas, nombres repetidos o que colisionan con
  la regla general.
- Con `folder_data/` (4 XML) el plan da **3 proyectos**: «Modelo DDV»
  [manifest] ×2 archivos (orígenes CPYBCA / Otros), «UDV INT FISICO» y
  «UDV INT LOGICO» [archivo]; 13 pasos con 3 gates.

**Secuencia manual equivalente** (lo que el orquestador hace por dentro; útil
para diagnosticar o para el notebook corporativo):
```
0)    create_admin                                        # cuenta admin + 4 roles + whitelist de Modeladores (idempotente)
1..N) por CADA proyecto y CADA uno de sus XML:
      quality → crosscheck --project "P" → migrate --project "P" [--description "…"] (dry-run) → --apply
Al final, por CADA proyecto:
      arrange_all --project "P"
      audit_data_consistency                               # esperado: 0 fixables
      seed_ddl_export_rules --project "P" (o --all-projects) → --apply
      mark_base_version (dry-run) → --apply                # v1 de cada proyecto activo
```
Como los ids son deterministas (`uuid5("<projectId>|<Long_Id>")`), re-correr
el mismo XML al mismo proyecto actualiza en su sitio. Tras toda carga que
mueva estándares (glosario/dominios/UDP) de un proyecto, registrar una
baseline nueva de Data Standards EN ESE proyecto antes de usar Restore.

**Plan B corporativo (Lakebase inalcanzable desde la laptop — private link):**
la misma secuencia corre desde un cluster del PROPIO workspace (access mode
**Dedicated**) con el notebook `scripts/databricks/carga_erwin_notebook.py`.
No reimplementa nada: invoca estos mismos scripts vía subprocess (detalle en
el doc 35 §4.1 de plan-implementacion/).

**Requisitos:** el `.venv` del backend (el parseo usa solo stdlib; `migrate
--apply` y `arrange_all` usan la conexión Lakebase del `.env`
(Databricks Lakebase Postgres vía `app/core/db/sync.get_sync_db()`).
`arrange_all` necesita además **node** y el elkjs del front (o `ELKJS_PATH`).
Todos se ejecutan **desde la raíz del backend**. Los comandos y flags de esta
guía NO cambiaron con la migración de BD.

---

## 1. `quality` — gate de calidad PRE-migración

Analiza el XML contra las reglas de la plataforma y reporta qué haría la
migración con cada incongruencia. No toca la BD.

```bash
.venv/bin/python -m scripts.erwin_migration.quality "ruta/modelo.xml" [más.xml ...] [--json salida.json]
```
- **Entrada:** 1..N XMLs. `--json` vuelca el detalle para automatizar.
- **Salida esperada:** conteos del modelo + hallazgos por severidad:
  - `ERROR` — requiere decisión manual (la migración lo omite o corta).
  - `WARN` — la migración lo resuelve sola con política aprobada
    (p.ej. tabla sin schema → `No_Definido`; duplicados internos → gana la
    copia más usada y las demás quedan como ALIAS; vista sin fuente →
    descartada — políticas owner 2026-07-24, doc 32b).
  - `INFO` — pérdida aceptada o particularidad (p.ej. particiones PART_nn
    con correlativo incongruente → se marcan igual, orden efectivo físico).
- **Exit code 1 si hay ERRORs** (sirve de gate en CI o corridas encadenadas).

## 1b. `crosscheck` — gate 2: el archivo CONTRA la BD viva (y entre archivos)

Lo que `quality` no puede ver: solapes con lo ya cargado y el veredicto de la
política multi-archivo. Solo lectura; correr SIEMPRE antes de un `--apply`
incremental.

```bash
.venv/bin/python -m scripts.erwin_migration.crosscheck "a.xml" [b.xml ...] [--json out.json] [--no-db]
```
- Por archivo: duplicados internos (con score y si las copias son idénticas),
  particiones a reasignar (R6), defs UDP sin uso (candidatas A4).
- Contra BD: tablas/vistas/schemas solapados con veredicto **ADOPTA/ACTUALIZA**
  (score de uso de cada lado) y diff de columnas; glosario/dominios nuevos o
  distintos; enums UDP que crecerían y variantes de grafía ignoradas (A3);
  **estimación de la carga** (upserts, lotes, minutos según latencia medida).
  Doc 75: el cruce es contra el PROYECTO destino (`--project`, default = nombre
  del modelo); un proyecto que no existe todavía = todo nuevo.
- Con 2+ archivos: solape de tablas entre ellos.

## 2. `extract_standards` — estándares limpios a archivos

Extrae **glosario (NSM), parent domains y definiciones UDP** del XML a JSON
(+ CSV opcional). Fiel al archivo, sin BD, sin obfuscación.

```bash
.venv/bin/python -m scripts.erwin_migration.extract_standards "ruta/modelo.xml" [--out DIR] [--csv]
```
- **Salida** (default `standards_out/<xml>/`): `glossary.json`
  (`term/abbrev/alts`), `parent_domains.json` (`name/dataType/definition`),
  `udp_definitions.json` (`name/level/view/dataType/default/allowedValues/usedBy`)
  y `summary.json`. Con `--csv`, equivalentes abribles en Excel.
- **Facetas (doc 69, 2026-09-05):** las defs `Entity.Logical.X` y
  `Entity.Physical.X` de Erwin son definiciones DISTINTAS de la plataforma
  (`view` = `logical` | `physical`; View y Model siempre físicas). La carga
  escribe los valores de ambas facetas en el mismo `udpValues` (ids distintos)
  y captura por columna el tipo lógico, el orden lógico y los flags
  `Is_Logical_Only`/`Is_Physical_Only`; el `defaultDataType` del dominio es el
  tipo FÍSICO y el lógico va en `logicalDataType`. Ver
  `scripts/erwin_migration/README.md` § Facetas.
- **Nota (actualizado doc 22 §7):** los `allowedValues` de UDP tipo lista se
  leen de la **lista EXPLÍCITA** del XML (`tag_Udp_Values_List`) — el catálogo
  completo de la definición, aunque un valor no se use en ninguna tabla —
  unido a los valores observados. (Antes solo se tomaban los USADOS, lo que
  truncaba enums como "Clasificación del Dato" a las categorías presentes;
  corregido en el parser — toda migración nueva ya trae la lista completa.)

## 3. `extract_model` — modelo de datos limpio a archivos

El complemento de estándares: **tablas + columnas, vistas con orígenes,
relaciones FK y canvases** por subject area. Fiel al archivo, aplicando las
mismas políticas neutras de la migración (schema faltante → `No_Definido`,
columnas duplicadas → se queda la 1ª).

```bash
.venv/bin/python -m scripts.erwin_migration.extract_model "ruta/modelo.xml" [--out DIR] [--csv]
```
- **Salida** (default `model_out/<xml>/`): `tables.json` (con columnas:
  físico/lógico, tipo, nullable, PK/FK, dominio, udpValues), `views.json`
  (columna a columna con `sourceTable`/`sourceColumn`), `relationships.json`
  (identifying/non-identifying + pares FK), `canvases.json`,
  `subject_areas.json`, `summary.json`. Con `--csv`: planos
  `tables/columns/views/relationships.csv` (1 fila por columna / par).
- **Referencia** (DDV real): 163 tablas · 4.564 columnas · 119 vistas ·
  134 relaciones · 28 canvases, ~10 s.

## 4. `migrate` — carga real a la plataforma ("Erwin one-shot")

Escribe el modelo **fiel** (sin obfuscar) directo a las colecciones
publicadas. **Sin `--apply` es dry-run**: parsea, corre el quality gate y
muestra el plan sin abrir conexión a la BD.

```bash
.venv/bin/python -m scripts.erwin_migration.migrate "ruta/modelo.xml" [más.xml ...] \
    [--apply] [--project "Nombre"] [--description "…"] [--only-sa SUBJECT_AREA] [--force]
```
- **Jerarquía:** Modelo → proyecto · Subject Area → folder · ER_Diagram →
  canvas (`tableIds` + `viewIds`: las vistas dibujadas en el diagrama son
  miembros de ese canvas, doc 70). `--project` fija el proyecto destino
  (obligatorio si ya existe; `--description` sólo si se crea); `--only-sa`
  migra solo una SA (pilotos); `--force` continúa aunque el gate tenga ERRORs.
- **Proyecto PRIMERO (doc 75):** `Migrator` resuelve/crea el proyecto antes de
  cualquier doc (`_ensure_project`), estampa `projectId` en TODO (modelo y
  estándares), namespacea los ids (`policies.project_scoped_id`) y acota el
  prefetch de claves naturales, la adopción y la unicidad al proyecto. Los
  estándares se **reusan por clave natural DENTRO del proyecto** (unión
  distinta entre archivos: el primero gana; un `abbrev` o `defaultDataType`
  distinto en otro archivo queda en `glossary_conflicts`/`domain_conflicts` del
  reporte y en la hoja «Dominios en conflicto» de `migration_detail_report`);
  el `naming_config` del proyecto se siembra con `$setOnInsert`.
- **Relaciones v2 (doc 19):** UN doc por relación Erwin con TODOS sus pares
  de columnas (`parentTableId/childTableId + pairs[]` — las FK compuestas ya
  no se parten en N docs) + `identifying` (tipo 2) y cardinalidad del hijo.
- **Cardinalidad del PADRE (fix 2026-07-17, doc 21):** sale del
  `Null_Option_Type` de la relación — `"100"` (Nulls Allowed: la FK del hijo
  admite NULL) → `zero-one` (el rombo del diagrama Erwin); `"101"`/otro →
  `one`. Antes se hardcodeaba `one`. Evidencia DDV: las 35 identifying
  (FK⊂PK) traen todas `"101"`; las non-identifying, 95 con `"100"` y 4 con `"101"`.
- **Partición nativa (2026-07-17, doc 21):** el UDP de columna `Particion`
  con valor `PART_nn` (convención DDV; el correlativo es el orden) marca
  `isPartition` — SOLO si la tabla es congruente (correlativos sin duplicar
  y en orden físico; el DDL de la plataforma emite `PARTITIONED BY` en orden
  físico). Incongruente → no se marca, warning y queda en el audit (C10)
  para decisión humana. `"No Definido"` (default del UDP) no es partición.
  Las incongruencias del XML DDV actual ya tienen decisión del owner: §7.
- **Entidad `schemas` (doc 18):** también upserta un doc por schema usado
  (ids `sch-<name>` namespaceados por proyecto, reusa por nombre
  case-insensitive dentro del proyecto).
- **Definiciones de vista (F5, doc 22):** además del físico, migra la
  definición funcional a nivel VISTA (`views.description`) y a nivel COLUMNA
  DE VISTA (`sources[].description`) — esta última **solo** cuando difiere de
  la definición de la columna física origen (si coincide, la columna de vista
  HEREDA y no se guarda override).
- **PKs correctas:** los miembros del Key_Group PK se traducen vía
  `Key_Group_Member.Attribute_Ref` (fix 2026-07-16 — antes NINGUNA columna
  migrada quedaba `isPrimaryKey`).
- **Política de RE-RUN sobre el mismo XML:** los datos del MODELO (órdenes de
  columnas y de llave incluidos) los pisa el XML — re-correr = re-sincronizar
  desde Erwin; los LAYOUTS de canvas trabajados en la plataforma se PRESERVAN.
  El `views.sql` (CREATE VIEW original) es referencia congelada: el Export DDL
  de la plataforma siempre genera desde la estructura (`sources`).
- **DOS órdenes distintos (doc 19 §12b · doc 74):**
  1. *Orden único de columnas* (`ordinal`, el mismo en el modelo lógico y el
     físico): las llaves primarias primero, en el orden de la llave, y después
     el resto en el Column order de Erwin (`Columns_Order_Ref_Array`; sin
     array cae al Attribute order y, sin éste, al `Physical_Order`). Es el
     «físico normal» que pidió el owner (PK al inicio); el orden físico de la
     BD de Erwin (`Physical_Columns_Order_Ref_Array`) ya no se persiste.
  2. *Orden de la LLAVE* (`pkPosition`, 0-based): el orden de miembros del
     Key_Group PK. Erwin ordena el bloque PK del diagrama y el
     `PRIMARY KEY(...)` del DDL por ESTE orden — la plataforma hace lo mismo
     (canvas + Export DDL).
- **Idempotente y NO destructivo:** ids deterministas
  (`uuid5("<projectId>|<Long_Id de Erwin>")`) → re-correr el mismo archivo al
  mismo proyecto actualiza en su sitio. Los estándares ya existentes del
  proyecto (glosario/dominios/UDP) se **reusan por clave natural**; las
  tablas/vistas en conflicto con docs creados a mano se **omiten con aviso**.
  Nunca borra datos.
- **Rendimiento real (2026-07-25):** el XML2 "Otros" (**1.8 GB**) cargó en
  **116 s con 97,577 escrituras** — buffers por colección con `bulk_write`
  en lotes de 1,000 (fast-path del adaptador Lakebase: 2 round-trips por
  lote) + prefetch de claves naturales.
- **Reporte de decisiones:** cada `--apply` deja
  `migration_report_<archivo>_<fecha>.json` en `migration-reports/`
  (adopciones, conflictos resueltos por score, alias, fusiones).
- **Después de `--apply`:** layout inicial en grilla → correr `arrange_all`
  (§5) y `audit_data_consistency` (§6). En una BD estrenada, cerrar con
  `seed_ddl_export_rules` (§6b) y `mark_base_version` (§6c).

## 5. `arrange_all` — auto-arrange ELK de todos los canvases

Re-organiza TODOS los canvases (`subject_areas.layout`) con elkjs — misma
librería y config que el botón Autoarrange del front. Incluye tablas **y
nodos de vista** (estimando el tamaño renderizado real, máx. entre naming
físico y lógico).

```bash
.venv/bin/python scripts/arrange_all.py [--project "Nombre"]
```
- **`--project`** (doc 32b): limita el arrange a los canvases de ESE proyecto.
- **Requiere:** node + elkjs. Resolución del bundle: env `ELKJS_PATH` →
  `../web-data-model-hub/node_modules/elkjs/...` (repo hermano) → `require`
  normal. Env opcional `ARRANGE_SCRATCH` para el directorio temporal.
- **Salida esperada:** `OK · N canvases re-organizados con ELK (tablas+vistas)`.

## 6. `audit_data_consistency` — validación post-carga

```bash
.venv/bin/python -m scripts.audit_data_consistency
```
Chequeos C1–C10 de integridad referencial y campos requeridos sobre la BD
(C1/C3 verifican además que cada columna/relación apunte a docs del MISMO
`projectId`, doc 75). Esperado tras una migración limpia: **0 hallazgos
fixables** (más los informativos conocidos: C10 particiones reasignadas, C5
vistas multi-fuente, C8b grafías variantes fieles al XML).

## 6b. `seed_ddl_export_rules` — ruleset base del DDL Export

Siembra, como UNA versión de Data Standards del proyecto ("Base — DDL export
rules"), las **14 reglas activas** del Export DDL que reproducen los 7 DDL de
la macro BCP (doc 76 de plan-implementacion/): `-- DROP` comentado,
TBLPROPERTIES de vacuum, tags `updateFrecuency`/`isDAC`/`DAC`, tabla de
rechazos `_rej` (+ `tiporeject`, particiones con su tipo), vistas técnicas
NoDAC/DAC (`{tabla}` / `{tabla}dac`), vistas de rechazos NoDAC/DAC y la
desencriptación `bcp_encrypt_function.decrypt_column_view` en las vistas DAC y
de negocio, **más los lookups `vacuum_map` / `update_frequency_map` /
`dac_flag_map` / `dac_map`** (el «case when» de la macro). Doc 75: el ruleset
es POR PROYECTO — `--project "P"` o `--all-projects` (obligatorio uno); en un
proyecto que ya tiene reglas se salta con aviso.

```bash
.venv/bin/python -m scripts.seed_ddl_export_rules --all-projects           # dry-run
.venv/bin/python -m scripts.seed_ddl_export_rules --project "Modelo DDV" --apply
```
- **Fix corporativo (2026-07-30, doc 36 anexo):** el kit multi-archivo NO
  crea defs UDP con usedBy=0 (política A4) y en el DDV real ninguna tabla
  asigna "Tipo de Vista" — la 1ª corrida en el workspace corporativo falló
  con `Unknown UDP 'Tipo de Vista'`. Resuelto EN el script: la seed auto-crea
  en el MISMO batch la def que el kit podría omitir — **"Frecuencia Vacuum"**
  (table, list, allowedValues = las 9 claves de `vacuum_map`, default
  `CUSTOM_90 days`) y enlaza a ella todos los lookups que nacen de ese UDP.
  Idempotente. Desde el doc 76 la vista técnica ya no depende de "Tipo de
  Vista" (la macro la genera siempre) y esa def dejó de auto-crearse.
  Cualquier OTRO UDP que las reglas, los lookups o el `partitionOrderUdp`
  del layout necesiten y falte («Clasificacion del Dato» de tabla y columna,
  «Particion») = modelo sin migrar → aborta limpio con la lista.

## 6c. `mark_base_version` — marcador de versión base v1 (cierre OBLIGATORIO)

NUEVO 2026-07-29. La migración escribe DIRECTO a las colecciones publicadas
sin crear changesets, y sin una versión aplicada la web **bloquea el módulo
Model** ("Open model" exige producción publicada). Doc 75: las versiones son
POR PROYECTO — el script recorre los proyectos activos y, en cada uno que no
tenga versiones aplicadas, crea el changeset marcador **`v1`** (status
`approved` + `appliedAt`, **0 cambios**, `projectId` del proyecto) y registra
la baseline de Data Standards si el stream de ese proyecto está vacío; además
asegura el permiso `rollback` en los roles. Idempotente (un proyecto con `v1`
se salta). Con una familia multi-archivo: TODOS los `migrate --apply` del
proyecto primero, el marcador después (`--title` para el rótulo).

```bash
.venv/bin/python -m scripts.mark_base_version           # dry-run
.venv/bin/python -m scripts.mark_base_version --apply
```

## 7. `fix_particiones_ddv_20260717` — decisiones del owner (solo XML DDV actual)

El único script NO agnóstico, a propósito: re-aplica las 3 correcciones de
partición **decididas tabla por tabla por el owner** (doc 21 §3) sobre el DDV
real — datos de negocio que NO están en el XML, así que **re-migrar el mismo
XML las pisa** (el audit C10 lo avisa). Correr DESPUÉS de re-migrar
`DDV - CPYBCA.xml`; para cualquier otro XML no aplica. Doc 75: las tablas se
buscan en el proyecto `--project` (default «Modelo DDV»).

```bash
.venv/bin/python -m scripts.fix_particiones_ddv_20260717           # dry-run
.venv/bin/python -m scripts.fix_particiones_ddv_20260717 --project "Modelo DDV" --apply
```

## 7b. `reset_to_base_version` — volver a la v1 de un proyecto

Deshace y borra toda versión posterior a `v1` **del proyecto** (`--project
"P"`; sin él, todos los activos), dejando producción como la dejó la carga.
Dry-run por default; `--apply` escribe.

```bash
.venv/bin/python -m scripts.reset_to_base_version --project "UDV INT FISICO"
.venv/bin/python -m scripts.reset_to_base_version --project "UDV INT FISICO" --apply
```

---

## 8. Librerías internas (no se ejecutan directo)

- **`erwin_parser.py`** — parser streaming (`iterparse`, 1 pase, namespace con
  comodín) del XML nativo → dataclasses neutras (`ErwinModel`). No decide
  nada de negocio.
- **`policies.py`** — políticas aprobadas de migración (doc 12): schema
  faltante → `No_Definido`, dedup de columnas (gana la 1ª), colapso de defs
  UDP Logical/Physical, mapeo de niveles UDP (Entity→table, Attribute→column,
  Model→canvas), ids deterministas uuid5 namespaceados por proyecto
  (`project_scoped_id(project_id, long_id)`, doc 75).
- **`scripts/run_migration.py`** — orquestador (doc 54/75): lee el
  manifiesto (`ManifestError`), planifica archivos → proyectos
  (`plan_files`/`group_by_project`), corre el gate `quality` por proyecto y
  encadena los scripts de arriba.

## 9. Qué garantiza el agnosticismo (y sus límites)

Funciona con cualquier export "Save As XML" de Erwin 10.x. Degradaciones
previstas (avisadas por el gate, nunca rompen):
- **Sin `Hive_Database`** (modelos no-Hive): las tablas caen al schema
  `No_Definido` (WARN). El resto migra normal.
- **Sin NSM embebido**: glosario = 0 términos (no es error).
- **UDPs de nivel View u otros owners** fuera de Entity/Attribute/Model: no
  se migran (INFO del gate).
- **Macros `%...%` en nombres físicos**: se resuelven vía
  `User_Formatted_Physical_Name`.
- Vistas: Erwin no les da nombre lógico; se migran con el físico.

## 10. Censo: qué del XML NO se incorpora hoy (doc 22 §7)

Inventario de datos presentes en el XML de Erwin que la plataforma **aún no
modela**. No son bugs: son alcance de producto. Útil como contexto para una
migración o para futuras features.

| Dato del XML | Estado | Detalle |
|---|---|---|
| Enum de `allowedValues` de UDP tipo lista | **RESUELTO** | El parser lee la lista completa `tag_Udp_Values_List` (antes solo los usados). Ver §2. |
| Definiciones de vista/columna-de-vista | **RESUELTO** (F5) | `views.description` + `sources[].description`. Ver §4. |
| Valores UDP de nivel **Domain** (~59 en el DDV) | **Abierto** | Los `Property_Type` con owner de dominio no se migran (solo Entity/Attribute/Model → table/column/canvas). |
| **RI actions** de las relaciones (~134 rels) | **Abierto** | ON DELETE/UPDATE (Cascade/Restrict/…) del `Referential_Integrity` no se modela; solo se migra cardinalidad + identifying. |
| **Índices** (`Key_Group` IF*) + `Is_Unique` | **Abierto** | Los índices no-PK y el flag de unicidad no se migran (no hay feature de índices en la web). |
| **Comment ≠ Definition** (~125 columnas) | **Abierto** | Erwin distingue `Comment` y `Definition`; la plataforma guarda **uno** (`definition or comment`). Cuando difieren, se pierde el que no se elige. |

> Las decisiones sobre los ítems **Abiertos** las toma el owner; a la fecha
> (2026-07-18) quedaron descartados hasta nuevo aviso.

---

## Ver también
- `esquema-datos.md` — referencia completa de las colecciones destino (campos,
  tipos, embebidos, referencias) que estos scripts pueblan.
- `arquitectura.md` §6.1 — el alcance `VERSIONED` y el flujo de publicación.
