# Migración Erwin → Data Model Hub — guía de scripts

**Actualizado:** 2026-07-24 · Aplica a exports **"Save As XML" de Erwin 10.x**
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
| Vaciar modelo+estándares+governance ANTES de una carga limpia | `reset_for_migration` | Sí (con `--apply`) — preserva users/roles/naming_config/column_catalog/audit_log |
| Cargar el modelo REAL a la plataforma | `migrate` | Sí (con `--apply`) |
| Ordenar los canvases después de cargar | `arrange_all` | Sí |
| Validar consistencia después de cargar | `audit_data_consistency` | Solo lee |
| Crear el usuario `admin` (BD nueva, para poder entrar) | `create_admin` | Sí (upsert) |
| Re-aplicar las 3 correcciones de partición del owner (solo XML DDV actual) | `fix_particiones_ddv_20260717` | Sí (con `--apply`) |
| Volver a la VERSIÓN BASE (deshace y borra toda versión posterior a v1) | `reset_to_base_version` | Sí (con `--apply`) |

**Flujo recomendado para un XML nuevo:**
```
quality  →  crosscheck (vs BD)  →  migrate (dry-run)
        →  (opcional si se REEMPLAZA la BD: reset_for_migration --apply)
        →  migrate --project "Familia" --apply
        →  arrange_all --project "Familia"  →  audit_data_consistency
```
Corrido el 2026-07-16 contra el DDV real: la BD de la plataforma ES ese modelo.
(La demo sintética y sus seeds se retiraron el 2026-07-20 — el flujo es
únicamente XML → Lakebase.)

**Requisitos:** el `.venv` del backend (el parseo usa solo stdlib; `migrate
--apply` y `arrange_all` usan la conexión del `.env` según `DB_BACKEND` —
`lakebase` = Databricks Lakebase Postgres vía `app/core/db/sync.get_sync_db()`).
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
- Con 2+ archivos: solape de tablas entre ellos.

## 2. `extract_standards` — estándares limpios a archivos

Extrae **glosario (NSM), parent domains y definiciones UDP** del XML a JSON
(+ CSV opcional). Fiel al archivo, sin BD, sin obfuscación.

```bash
.venv/bin/python -m scripts.erwin_migration.extract_standards "ruta/modelo.xml" [--out DIR] [--csv]
```
- **Salida** (default `standards_out/<xml>/`): `glossary.json`
  (`term/abbrev/alts`), `parent_domains.json` (`name/dataType/definition`),
  `udp_definitions.json` (`name/level/dataType/default/allowedValues/usedBy`)
  y `summary.json`. Con `--csv`, equivalentes abribles en Excel.
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
    [--apply] [--project "Nombre"] [--only-sa SUBJECT_AREA] [--force]
```
- **Jerarquía:** Modelo → proyecto · Subject Area → folder · ER_Diagram →
  canvas. `--project` renombra el proyecto; `--only-sa` migra solo una SA
  (pilotos); `--force` continúa aunque el gate tenga ERRORs.
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
  (ids `sch-<name>`, reusa por nombre case-insensitive).
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
- **DOS órdenes distintos (fix 2026-07-17, doc 19 §12b):**
  1. *Orden físico de columnas* (`ordinal`): sale del array ordenado del dueño
     (`Physical_Columns_Order_Ref_Array` — lo que Erwin muestra), con fallback
     al `Physical_Order` numérico (puede quedar desincronizado tras
     reordenamientos).
  2. *Orden de la LLAVE* (`pkPosition`, 0-based): el orden de miembros del
     Key_Group PK. Erwin ordena el bloque PK del diagrama y el
     `PRIMARY KEY(...)` del DDL por ESTE orden, no por el físico — la
     plataforma hace lo mismo (canvas + Export DDL).
- **Idempotente y NO destructivo:** ids deterministas (uuid5 del Long_Id de
  Erwin) → re-correr el mismo archivo actualiza en su sitio. Los estándares
  ya existentes (glosario/dominios/UDP) se **reusan por clave natural**; las
  tablas/vistas en conflicto con docs creados a mano se **omiten con aviso**.
  Nunca borra datos.
- **Después de `--apply`:** layout inicial en grilla → correr `arrange_all`
  (§5) y `audit_data_consistency` (§6).

## 5. `arrange_all` — auto-arrange ELK de todos los canvases

Re-organiza TODOS los canvases (`subject_areas.layout`) con elkjs — misma
librería y config que el botón Autoarrange del front. Incluye tablas **y
nodos de vista** (estimando el tamaño renderizado real, máx. entre naming
físico y lógico).

```bash
.venv/bin/python scripts/arrange_all.py
```
- **Requiere:** node + elkjs. Resolución del bundle: env `ELKJS_PATH` →
  `../web-data-model-hub/node_modules/elkjs/...` (repo hermano) → `require`
  normal. Env opcional `ARRANGE_SCRATCH` para el directorio temporal.
- **Salida esperada:** `OK · N canvases re-organizados con ELK (tablas+vistas)`.

## 6. `audit_data_consistency` — validación post-carga

```bash
.venv/bin/python -m scripts.audit_data_consistency
```
Chequeos C1–C9 de integridad referencial y campos requeridos sobre la BD.
Esperado tras una migración limpia: **0 hallazgos fixables**.

## 7. `fix_particiones_ddv_20260717` — decisiones del owner (solo XML DDV actual)

El único script NO agnóstico, a propósito: re-aplica las 3 correcciones de
partición **decididas tabla por tabla por el owner** (doc 21 §3) sobre el DDV
real — datos de negocio que NO están en el XML, así que **re-migrar el mismo
XML las pisa** (el audit C10 lo avisa). Correr DESPUÉS de re-migrar
`DDV - CPYBCA.xml`; para cualquier otro XML no aplica.

```bash
.venv/bin/python -m scripts.fix_particiones_ddv_20260717           # dry-run
.venv/bin/python -m scripts.fix_particiones_ddv_20260717 --apply
```

---

## 8. Librerías internas (no se ejecutan directo)

- **`erwin_parser.py`** — parser streaming (`iterparse`, 1 pase, namespace con
  comodín) del XML nativo → dataclasses neutras (`ErwinModel`). No decide
  nada de negocio.
- **`policies.py`** — políticas aprobadas de migración (doc 12): schema
  faltante → `No_Definido`, dedup de columnas (gana la 1ª), colapso de defs
  UDP Logical/Physical, mapeo de niveles UDP (Entity→table, Attribute→column,
  Model→canvas), ids deterministas uuid5.

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
