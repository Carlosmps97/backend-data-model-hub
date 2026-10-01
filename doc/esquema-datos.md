# Esquema de datos — Data Model Hub (referencia completa de colecciones)

**Actualizado:** 2026-09-30 (doc 105: jobs de la carga Excel en la BD, marcas nuevas de la cabecera del changeset y valores de UDP por tipo, con los números en su forma canónica) · Fuente: los modelos Pydantic `*Doc` reales de `app/features/*/models.py` + `app/core/models.py`, los `repository.py` (nombres de colección) y `app/core/db/indexes.py` (índices).

Referencia **campo por campo** de todas las colecciones que administra este backend. Describe el **estado actual**, no cómo migrar. Complementa `arquitectura.md` (§7, visión) y `migracion-erwin.md` (carga desde XML de Erwin).

> **Residencia física:** la BD productiva y única es **Databricks
> Lakebase Postgres** (`databricks_postgres`, schema PG `dmh`): cada colección
> de este documento vive como una tabla `(id text PRIMARY KEY, doc jsonb,
> project_id text GENERATED ALWAYS AS (doc->>'projectId') STORED)` con el
> documento COMPLETO (con `_id`) en `doc` + índice GIN `jsonb_path_ops` +
> btrees por expresión para los sorts. La columna generada `project_id`
> (doc 75 D19) es la traducción física del alcance por proyecto: una igualdad
> o `$in` sobre `projectId` compila a `project_id = …` (`COLUMN_FIELDS` del
> traductor) y los índices compuestos la llevan como columna líder.
> `ensure_indexes()` declara además los índices lógicos (entre ellos el
> wildcard `udpValues.$**` en tablas, columnas y canvases; el compuesto
> `changeset_changes(csId, collection)`; y `standards_versions(projectId, seq)`,
> el ÚNICO unique de todo el sistema). La forma lógica de los documentos — TODO
> lo que sigue — es independiente del almacén físico: el acceso está confinado
> tras el adaptador `app/core/db/lakebase/`, que expone una superficie de
> consulta con el vocabulario de `pymongo` sobre las tablas JSONB (no conecta a
> Mongo).

---

## 0. Convenciones globales (aplican a TODA colección)

- **`model_config = DOC_CONFIG`** (`app/core/models.py`): `ConfigDict(extra="ignore", populate_by_name=True)`.
  - `extra="ignore"` ⇒ al **leer**, cualquier campo del doc persistido que NO esté declarado en el `*Doc` se **descarta silenciosamente** (no llega a la app). Implicación de migración: para no perder datos hay que mirar el **doc real en la BD**, no solo el modelo — puede haber campos legacy o aditivos (p. ej. `migratedFrom`, `erwinLongId`, `flgactive`, `deletedAt`) que persisten en el doc pero el modelo ignora.
  - `populate_by_name=True` ⇒ los campos con `alias` aceptan el nombre del atributo o el alias en la entrada.
- **`_id` vs `id`**: el repositorio guarda el `id` (o la clave natural) del modelo como `_id` del doc al insertar, y lo revierte a `id` al leer. Donde el `_id` es especial se anota en cada colección.
- **Soft-delete**: la mayoría **no borra**; marca `flgactive: false` + `deletedAt` (ISO) y las lecturas filtran `flgactive != false`. Doc 100: un upsert publicado sobre una entidad borrada la **reactiva** y le quita `deletedAt`/`deletedIn` (antes quedaba activa con su marca de borrado vieja); un payload nunca fija esas marcas. Excepciones: `audit_log` es append-only; `changesets` / `changeset_changes` / `standards_versions` no usan `flgactive` (se gobiernan por estado/seq).
- **Alcance por proyecto (doc 75 D1)**: todo documento que describe el modelo o sus estándares pertenece a exactamente UN proyecto y lleva `projectId` (ref `projects.id`), estampado SIEMPRE server-side (nunca lo elige el cliente). `app/core/scope.py` es la única forma de armar un filtro de alcance (`scoped(pid, flt)`); `PROJECT_SCOPED` enumera las 16 colecciones con `projectId` y `published()` del ledger rechaza (`MissingProjectError` → 500, bug de programación) una lectura de esas colecciones sin `projectId`/`_id`/`tableId` en el filtro. Sólo `projects` (la raíz del alcance), `users`, `roles`, `changeset_changes` y `audit_log` no lo llevan.
- **Sin integridad referencial**: todas las referencias entre colecciones son **strings** (un `id`, o a veces un **nombre**). El almacén no las valida (el `doc` jsonb es opaco para Postgres) — la consistencia la garantiza la app. Ver §Referencias (final).
- **Timestamps**: strings ISO-8601 (`datetime.now(timezone.utc).isoformat()`), nunca tipos fecha nativos del almacén (`timestamptz`).
- **Trazabilidad de migración Erwin**: los docs cargados desde XML llevan además `migratedFrom: "erwin"` + `erwinLongId` (no declarados en el modelo → se ignoran al leer, pero **persisten** en el doc).

**Notación de las tablas de campos:** `str?` = opcional/nullable; `dict[str,str]` = mapa embebido; “ref X.y” = referencia por string a la colección X campo y.

---

## 1. Mapa de colecciones (26 propias)

| Colección | Modelo `*Doc` | `projectId` | Versionada (changeset) | Soft-delete | `_id` |
|---|---|:--:|:--:|:--:|---|
| `projects` | ProjectDoc | — (es la raíz) | ✅ | ✅ (+ `deletedIn`) | `id` (uuid) |
| `folders` | FolderDoc | ✅ | ✅ | ✅ | `id` (uuid) |
| `subject_areas` (canvases) | SubjectAreaDoc | ✅ | ✅ | ✅ | `id` (uuid) |
| `schemas` | SchemaDoc | ✅ | ✅ | ✅ | `id` (`sch-<name>` en migración, namespaceado por proyecto) |
| `canonical_tables` | CanonicalTableDoc | ✅ | ✅ | ✅ | `id` (uuid) |
| `canonical_columns` | CanonicalColumnDoc | ✅ | ✅ | ✅ | `id` (uuid) |
| `relationships` | RelationshipDoc | ✅ | ✅ | ✅ | `id` (uuid) |
| `views` | ViewDoc | ✅ | ✅ | ✅ | `id` (uuid) |
| `changesets` | ChangesetDoc | ✅ | — (es el mecanismo) | — | `id` (uuid) |
| `changeset_changes` | ChangeDoc | — (hereda del changeset) | — | — | **determinista** `{csId}::{collection}::{entityId}` |
| `standards_versions` | StandardsVersionDoc | ✅ | — (versionado propio) | — | `id` (uuid); único por `(projectId, seq)` |
| `parent_domains` | ParentDomainDoc | ✅ | — (Data Standards) | ✅ | `id` (uuid) |
| `glossary_terms` | AbbreviationDoc | ✅ | — (Data Standards) | ✅ | `id` (uuid) |
| `udp_definitions` | UdpDefinitionDoc | ✅ | — (Data Standards) | ✅ | `id` (uuid) |
| `naming_config` | NamingConfigDoc | ✅ | — (Data Standards) | — | **`<projectId>:<scope>`** (clave natural; NO hay campo `id`) |
| `ddl_rules` | DdlRuleDoc | ✅ | — (Data Standards) | ✅ | `id` (uuid) |
| `ddl_ruleset_config` | DdlRulesetConfigDoc | ✅ | — (Data Standards) | — | **`<projectId>`** (un doc por proyecto) |
| `upload_profiles` | UploadProfileDoc | ✅ | — (config operativa, sin versionado) | ✅ | `id` (uuid) |
| `sheet_templates` | SheetTemplateDoc (en `reporting/sheet_templates/models.py`) | ✅ | — (config operativa, sin versionado) | ✅ | `id` (uuid) |
| `upload_jobs` | (sin modelo; `bulk_upload/jobs.py`) | — (lleva `csId`) | — (estado operativo, doc 105) | — (se borra por TTL) | `id` (uuid hex) |
| `upload_job_bodies` | (sin modelo; `bulk_upload/jobs.py`) | — | — (doc 105) | — (se borra con su job) | **= `_id` del job** |
| `deleted_changesets` | (sin modelo; `changesets/repository.py`) | — | — (lápidas, doc 105) | — (se borran al terminar o en la purga) | **`<csId>:<uuid>`** (una por intento de eliminación) |
| `users` | UserDoc | — | — | via `status=disabled` | **`username`** |
| `roles` | RoleDoc | — | — | — | **`key`** (slug del rol) |
| `saved_reports` | SavedReportDoc (en `reporting/query/reports.py`, no en `models/`) | ✅ | — | ✅ | `id` (uuid) |
| `audit_log` | (sin modelo) | — | — | append-only | `ObjectId` auto |

> **`column_catalog` fue RETIRADA (doc 54, 2026-08-22).** Pertenecía al planteamiento inicial de un agente conversacional embebido, hoy descartado por completo: el adaptador ya no la pre-crea y el reset destructivo (`scripts/reset_for_migration.py`) la elimina junto con todo el schema. No confundir con `canonical_columns` (el store real de columnas de esta app).
>
> **Entidades virtuales del reporting** (no son colecciones): `COLL_OF` en `reporting/query/executor.py` mapea `view_columns → views` y `models → subject_areas`.
>
> **Tablas físicas en Lakebase:** el adaptador pre-crea 19 tablas (`KNOWN_COLLECTIONS` en `app/core/db/lakebase/collection.py` = las 19 de este mapa); `ddl_rules` y `ddl_ruleset_config` (doc 30) y `upload_profiles` (doc 78: perfiles de carga — nombre, hojas con fila de cabecera y mapeos cabecera → campo / UDP, reglas por columna, políticas; un `isDefault` por proyecto; `origin` `user` | `builtin:plantilla-bcp`) y `sheet_templates` (doc 95 D11: plantillas de hoja Excel del Reporting) se crean on-demand con la misma forma `(id, doc jsonb)` + GIN; también `upload_jobs` y `upload_job_bodies` (doc 105, X1: el estado de los jobs de la carga Excel vive en la BD porque `app.yaml` corre `uvicorn --workers 2`) y `deleted_changesets` (doc 105, H2: lápidas de las eliminaciones de drafts).

---

## 2. Alcance del versionado

**`VERSIONED`** (`changesets/repository.py`, en orden de dependencia del apply): las 8 colecciones cuyos cambios pasan por el changeset (draft → submit → review → approve → publish):

```
projects · folders · subject_areas · schemas · canonical_tables · canonical_columns · relationships · views
```

Un cambio no toca la colección publicada hasta el **apply del approve**: vive como un doc en `changeset_changes` con el **doc completo** en `payload`. El `overlay(publicado, cambios)` produce el estado efectivo del draft. Desde el doc 105 (D1) es el ÚNICO camino de escritura de estas colecciones por la API: las rutas directas de canvases, carpetas, esquemas, vistas, relaciones y catálogo responden 409 `This change requires a version in edit mode.` (sólo el alta de un proyecto, `POST /api/projects`, sigue directa).

**Un changeset pertenece a UN proyecto** (`changesets.projectId`, doc 75 D2): cada cambio del draft debe ser de una entidad de ese proyecto (guard I1: `payload.projectId` lo estampa el servidor con el del changeset; una referencia a una entidad de OTRO proyecto — tabla, columna, dominio, esquema — es 409 `CrossProjectError`, guard I2). Las versiones (`versionLabel` `vN`), la producción vigente y el rollback son **por proyecto**: `v3` de «Modelo DDV» y `v3` de «UDV INT FISICO» son versiones distintas e independientes.

**Ciclo de vida del proyecto (doc 75 D5)**: `projects` sigue en `VERSIONED`, pero con asimetría: **crear** es directo (`POST /api/projects` = doc + estándares vacíos o copiados + marcador `v1`, en una request); **renombrar/describir/borrar** van SIEMPRE por un draft del propio proyecto (guard `entityId == cs.projectId`). El borrado es UN cambio `projects/<pid> op=delete`; al aplicarse (approve con unanimidad) el servidor corre `cascade_delete`: soft-delete (`flgactive:false` + `deletedAt` + `deletedIn:<csId>`) de TODO lo del proyecto (`CASCADE_COLLECTIONS` = `PROJECT_SCOPED` menos `changesets` y `standards_versions`, que se conservan como historial) y `$pull` del pid en `users.projectIds`. Después, toda ruta `/api/projects/{pid}/…` responde 404 «Project not found.» y las operaciones sobre sus changesets 409 «This project was deleted.». Restaurar un proyecto borrado está fuera de alcance.

**Data Standards** se versiona **aparte** (no por el changeset) y **por proyecto** (doc 75 D3): `parent_domains`, `glossary_terms`, `udp_definitions`, `naming_config` y (doc 30) `ddl_rules` + `ddl_ruleset_config` del proyecto se escriben directo a producción y cada apply/rollback deja una versión con **snapshot completo** en `standards_versions` con el `projectId` del proyecto; `seq`/`label` (`vN`) arrancan en 1 en cada proyecto. Las reglas DDL y su config mutan SOLO vía `POST /api/projects/{pid}/standards/apply` (`rulesUpsert`/`rulesDelete`/`ddlConfigPatch`); el router `/api/projects/{pid}/ddl-rules` es de lectura. Doc 105 (D1b): lo mismo para términos, dominios y naming — su CRUD directo, `propagate` y `rephysicalize` responden 409 —, un upsert con el id de un estándar de otro proyecto (o de uno borrado de este) responde 409 antes de escribir, y un apply sin cambios efectivos (upserts idénticos y bajas de ids ajenos o inexistentes se descartan) responde 422 sin registrar versión. Un proyecto nuevo nace con estándares vacíos o copia bloques (`glossary` · `domains` · `udp` · `naming` · `ddl`) de otro proyecto (`copyFrom`, versión `kind=copy`).

**No versionado**: `users`, `roles`, `saved_reports`, `audit_log`, `changesets`, `changeset_changes`.

---

## 3. Estructura (Model Explorer)

### `projects` — ProjectDoc
Raíz del alcance: es la ÚNICA colección del modelo sin `projectId`. `name` es único global (case-insensitive; 409 al crear). Un proyecto borrado por un draft aplicado queda `flgactive:false` + `deletedAt` + `deletedIn` (el changeset que lo borró) y nunca vuelve a listarse.
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK (`_id`) |
| name | str | — | único global (CI) |
| description | str? | null | |

### `folders` — FolderDoc
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK |
| projectId | str | — | ref `projects.id` (alcance, estampado server-side) |
| parentFolderId | str? | null | ref `folders.id` (None = raíz) |
| name | str | — | |
| order | int | 0 | orden en el árbol |

### `subject_areas` — SubjectAreaDoc  *(= “canvases”; rename pendiente)*
Un doc = **un diagrama ER** (canvas).
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK |
| projectId | str | — | ref `projects.id` |
| folderId | str? | null | ref `folders.id` (None = raíz del proyecto) |
| name | str | — | |
| tableIds | list[str] | [] | **membresía**: refs `canonical_tables.id` presentes en el canvas |
| layout | dict[str, NodePosDoc] | {} | posición por nodo: `{tableId | viewId: {x, y}}` |
| drawings | list[dict] | [] | capa DRAWING (shapes/texto con estilo); dicts free-form (sin sub-schema) |
| udpValues | dict[str,str] | {} | UDP nivel canvas: `{udpDefId: value}` |
| routes | dict[str, list[NodePosDoc]] | {} | **trazos manuales de wires** (doc 99): puntos de quiebre por wire, en coordenadas del canvas y en orden padre → hijo. La llave es el id del wire en el canvas: el de la relación (wire crow's-foot y rama de subcategoría) o `subsym-{símbolo}` (tronco). Un wire sin entrada usa el camino automático. Topes: 32 puntos por wire, 5 000 wires por canvas, coordenadas dentro de ±1 000 000, id de wire de hasta 200 caracteres. **Lectura tolerante** (un trazo corrupto se descarta y el canvas abre igual), **escritura estricta** en la entrada del cliente (422) |

**Embebido `NodePosDoc`**: `{ x: float, y: float }`.

### `schemas` — SchemaDoc
Esquema físico de BD como **entidad** (doc 18). Tablas y vistas lo referencian por **nombre** (string), no por id.
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK (migración: `sch-<name>` namespaceado por proyecto, reusa por nombre case-insensitive DENTRO del proyecto) |
| projectId | str | — | ref `projects.id` (doc 75: el esquema es una entidad DEL proyecto; el mismo nombre puede existir en dos proyectos) |
| name | str | — | único por proyecto (case-insensitive; índice `(projectId, name)`) |
| description | str? | null | |
| kind | `"tables"` \| `"views"`? | null | doc 44: qué contiene el esquema. Erwin NO lo trae (los `Hive_Database` del XML no llevan atributo/UDP que lo marque) — la UI lo pide al crear, la migración lo deriva de los MIEMBROS (mixto → tables) y `scripts/backfill_schema_kind.py` clasificó el stock por uso real (2026-08-14: 199 tables · 189 views · 0 sin clasificar). |

---

## 4. Catálogo canónico (pool DEL proyecto)

Desde el doc 75 el pool de tablas/columnas es **por proyecto**: `projectId` en cada doc, unicidad del nombre físico de tabla por proyecto (D6; `M_CLIENTE` puede existir en «Modelo DDV» y en «UDV INT FISICO»), lecturas por `/api/projects/{pid}/catalog/…` y validación de que un canvas/relación/vista sólo referencie tablas del mismo proyecto (guard I2).

### `canonical_tables` — CanonicalTableDoc
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK |
| projectId | str | — | ref `projects.id` (alcance) |
| physicalName | str | — | nombre físico (DDL); único por proyecto (CI, soft-delete aparte) |
| logicalName | str | — | nombre lógico (negocio) |
| **schema** | str? | null | **alias** de `sql_schema`; el nombre del esquema (ref `schemas.name` **por nombre**) |
| physicalNameOverridden | bool | false | doc 68: físico editado a mano (o heredado de la BD real) — el re-derivado del glosario no lo pisa; lo estampa el changeset (flag del payload o físico ≠ derivado) |
| logicalOnly | bool | false | doc 69: existe sólo en la faceta lógica (`Is_Logical_Only` de Erwin) |
| physicalOnly | bool | false | doc 69: existe sólo en la faceta física (`Is_Physical_Only`) |
| description | str? | null | definición funcional |
| udpValues | dict[str,str] | {} | `{udpDefId: value}` (valores de las etiquetas UDP nivel table) |

### `canonical_columns` — CanonicalColumnDoc
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK |
| projectId | str | — | ref `projects.id` (= el de su tabla) |
| tableId | str | — | ref `canonical_tables.id` |
| physicalName | str | — | por un changeset se graba con el `case` del naming del scope `column` (doc 83); doc 105 (rondas 4 y 5): un físico que ya existe para la columna —publicado o pendiente— y que el cambio repite tal cual se conserva aunque esté fuera de la regla |
| logicalName | str | — | |
| parentDomainId | str? | null | ref `parent_domains.id` (un dominio DEL MISMO proyecto) |
| dataType | str | — | tipo físico |
| typeOverridden | bool | false | true = `dataType` es override manual (no hereda del dominio) |
| logicalDataType | str? | null | doc 69: tipo LÓGICO (`dataType` sigue siendo el físico); `null` = no informado |
| logicalTypeOverridden | bool | false | override manual del tipo lógico respecto del `logicalDataType` del dominio |
| logicalOnly | bool | false | doc 69: existe sólo en la faceta lógica |
| physicalOnly | bool | false | doc 69: existe sólo en la faceta física |
| physicalNameOverridden | bool | false | doc 68: override manual del nombre físico (lo estampa el changeset: flag del payload o físico ≠ derivado); doc 105 (ronda 5): el físico existente que un cambio repite tal cual conserva su flag (el del payload o, si no viene, el del nombre existente) |
| isPrimaryKey | bool? | null | |
| isForeignKey | bool? | null | |
| isNullable | bool | true | |
| isPartition | bool | false | columna de partición (DDL emite `PARTITIONED BY`) |
| description | str? | null | definición funcional a nivel columna |
| physicalDescription | str? | null | doc 85: descripción FÍSICA (Comment de Erwin; el DDL la emite como COMMENT con fallback a `description`); `null` = igual a la lógica |
| ordinal | int | 0 | **orden único** de la columna en la tabla (el mismo en el modelo lógico y el físico; doc 74) |
| udpValues | dict[str,str] | {} | `{udpDefId: value}` |

> Un solo orden (doc 74 · doc 94): `ordinal`, único para ambas facetas, con las PK **siempre primero** y, entre PK, también por `ordinal` — no hay orden de llave aparte (`pkPosition` se retiró en el doc 94; un doc viejo que lo traiga lo pierde al leer). `PRIMARY KEY(...)` del DDL, bloque PK del canvas y vistas usan ese orden. La migración Erwin llena `ordinal` con «llaves primero (en el orden de la llave de Erwin) + Column order» (doc 74).

---

## 5. Relaciones y vistas

### `relationships` — RelationshipDoc
Un doc por relación Erwin con **todos** sus pares de columnas (FK compuesta = varios `pairs`). Ambos extremos son tablas del MISMO proyecto (guard I2).
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK |
| projectId | str | — | ref `projects.id` (alcance) |
| parentTableId | str | — | lado PK (“one”); ref `canonical_tables.id` |
| childTableId | str | — | lado FK (“many”); ref `canonical_tables.id` |
| pairs | list[RelationshipPairDoc] | (min 1) | pares de columnas PK↔FK |
| parentCardinality | str | "one" | enum CARDINALITIES (ver §12) |
| childCardinality | str | "zero-many" | enum CARDINALITIES |
| identifying | bool | false | relación **sólida**: la FK es parte de la PK del hijo |
| parentToChildPhrase | str? | null | frase padre→hijo (Erwin *Parent-to-Child Phrase*); se guarda recortada, vacío = null |
| childToParentPhrase | str? | null | frase hijo→padre (Erwin *Child-To-Parent Phrase*); ídem |

Las dos frases son la **etiqueta del wire** en el canvas y en el export (`padre→hijo / hijo→padre`, o la única que exista). No tienen validación dura de largo: el tope (120) vive en la caja de texto del front.

**Embebido `RelationshipPairDoc`**: `{ parentColumnId: str (ref canonical_columns.id), childColumnId: str (ref canonical_columns.id), roleName: str? }`. (`roleName` = rolename estilo Erwin; el nombre real de la columna vive en el hijo.)

> Docs legacy v1 (`sourceTableId`/`targetTableId`, un solo par) se **normalizan a v2** al leer (`_upgrade_legacy` → `parent/child` + `pairs`).

### `views` — ViewDoc
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK |
| projectId | str | — | ref `projects.id` (alcance; las fuentes son tablas del mismo proyecto) |
| name | str | — | |
| sql | str | "" | `CREATE VIEW` original (referencia congelada; el Export DDL regenera desde `sources` o emite `customSql`) |
| description | str? | null | definición funcional a nivel **vista** (F5) |
| tableId | str? | null | compat = `sourceTableIds[0]` |
| **schema** | str? | null | alias de `sql_schema` (ref `schemas.name` por nombre) |
| sources | list[dict] | [] | proyección **columna a columna** (ver shape abajo); list[dict] sin sub-schema estricto |
| outputAlias | str? | null | |
| expression | str? | null | |
| sourceTableIds | list[str] | [] | refs `canonical_tables.id`; el **orden** define los alias `t1, t2…` |
| showOnCanvas | bool | false | flag GLOBAL: la vista aparece en todo canvas con ≥1 fuente presente |
| customSql | str? | null | doc 61 → 91 → **doc 96 D6**: **User-Defined SQL** como texto **informativo** — se guarda tal cual, **sin validación** (p. ej. el `User_Defined_SQL` de Erwin o el JOIN/WHERE que el modelador anota) y **no se exporta**: el DDL de la vista sale siempre de `sources` (con 2+ fuentes `FROM t1, t2` sin JOIN). null ⇒ sin texto |
| udpValues | dict | {} | doc 61: `{udpDefId: value}` — keys de `udp_definitions` con `level='view'` (mismo contrato que tablas/columnas/canvas) |

> Doc 91 (2026-09-17): `tags`, `filter`, `joinOverride` y `customColumns` se retiraron (`extra="ignore"` descarta los valores legacy al leer; desaparecen al próximo write). Con 2+ fuentes el DDL lista `FROM t1, t2` sin JOIN (como Erwin).

**Shape de cada item de `sources`** (free-form; las claves nuevas persisten): `{ column?, tableId?, outputAlias?, expression?, castType?, description? }`.
- `column` = columna origen (por **nombre**); `tableId` = de qué fuente viene (F3 multi-fuente); `castType` = override de tipo (DDL emite `CAST(...) AS`); `description` = definición funcional propia de la columna-de-vista (F5, override del origen físico).

---

## 6. Versionado del modelo (changeset)

### `changesets` — ChangesetDoc
Cabecera del changeset (los cambios en sí viven en `changeset_changes`).
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK |
| title | str | — | |
| owner | str | — | userId (ref `users.id`) |
| status | str | "draft" | `draft` \| `submitted` \| `approved` \| `rejected` |
| description | str? | null | |
| versionLabel | str? | null | p. ej. `"v15"` (autoincremental **por proyecto**) |
| projectId | str | — | ref `projects.id` — el ÚNICO proyecto del changeset (doc 75 D2); lo fija el snapshot y no cambia |
| reviewers | list[str] | [] | userIds asignados |
| approvals | dict[str,dict] | {} | `{userId: {status:'approved'|'rejected', note?, at}}` |
| comments | list[dict] | [] | `{author, text, at}` |
| createdAt / updatedAt / submittedAt / reviewedBy / reviewedAt / reviewNote | str? | null | timestamps + compat de revisión single |
| appliedAt | str? | null | timestamp del apply EXITOSO a producción (`approved` sin `appliedAt` = el proceso murió a mitad; doc 105: `scripts/reapply_changeset.py` la devuelve a revisión — `recover_interrupted_publish`; un claim de menos de 30 min se saltea salvo `--force` — y el revisor re-aprueba). Se estampa con una transición condicionada al claim (`appliedAt: None` + `reviewedAt`): un publish cuya versión volvió a revisión en el medio no la deja «en revisión con `appliedAt`» |
| restoredFrom | dict? | null | doc 65: `{csId, versionLabel, appliedAt}` de la versión publicada que restaura un rollback |
| requests | list[dict] | [] | doc 88 §6: historial de solicitudes, un registro por envío |
| transfers | list[dict] | [] | doc 104: `{from, to, by, at, note?}` en orden; `owner` es el dueño ACTUAL y `transfers[0].from` quien la inició |
| partialApplyAt | str? | null | doc 104: un approve falló cuando YA escribía producción (parte pudo llegar): el draft no se elimina, se re-envía; re-editar no la borra. Doc 105: la pone también `recover_interrupted_publish` (conservadora) |
| uploadLock | dict? | null | doc 105 (X1): `{jobId, owner, at, heartbeat}` (epoch) de la carga Excel que ESTÁ escribiendo — «un apply por versión» para los dos procesos de uvicorn; transferir, eliminar y enviar a revisión lo respetan en su misma sentencia; un latido de más de 10 min = su proceso murió (se ignora) |
| restoreIncomplete | bool? | null | doc 105: draft de restauración mientras se graban sus inversos; si queda en `true` (el proceso murió), `submit` responde 409 y se elimina y restaura de nuevo |

> `deletesProject` NO se persiste: se **deriva en lectura** (existe el cambio `projects/<projectId> op=delete` en el ledger) y viaja en `version_row`, en el detalle del changeset y en `diff.impact.deletesProject` (`{projectId, name, counts}`) para el aviso crítico del aprobador (doc 75 D5/D20).

### `changeset_changes` — ChangeDoc
Un doc **por cambio**. `_id` determinista ⇒ last-write-wins por entidad.
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | — | PK determinista `{csId}::{collection}::{entityId}` |
| csId | str | — | ref `changesets.id` |
| collection | str | — | colección versionada afectada (una de `VERSIONED`) |
| entityId | str | — | id de la entidad en esa colección |
| op | str | — | `upsert` \| `delete` |
| payload | dict? | null | el **doc COMPLETO** de la entidad (para `upsert`) |
| at | str? | null | ISO: baseline de conflicto vs producción |
| before | dict? | null | imagen PREVIA de la entidad publicada (alimenta el rollback = draft inverso); el publish la estampa en lote antes de escribir (doc 105) |
| beforeAt | str? | null | `before=null` + `beforeAt` estampado = la entidad no existía (inverso = delete). Doc 105 (H4): re-editar la entidad en el draft ARRASTRA `before`/`beforeAt` (también en deletes); un re-approve conserva las ya estampadas sólo si la cabecera tiene `partialApplyAt` y, si no, las re-captura |
| origin | dict? | null | doc 51: procedencia del cambio (`paste`, `ctas`, `upload`…) |

`payload.projectId` lo estampa el servidor con el `projectId` del changeset (guard I1): un cliente no puede colar un doc de otro proyecto.

---

## 7. Data Standards (glosario, dominios, UDP, naming, reglas DDL) — versionado propio

### `standards_versions` — StandardsVersionDoc
Historial append-only; cada apply/rollback = una versión con snapshot completo.
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | — | PK (uuid) |
| projectId | str | — | ref `projects.id`: el stream de estándares es POR PROYECTO (doc 75 D3) |
| seq | int | — | monotónico **dentro del proyecto**; **índice único `(projectId, seq)`** — el ÚNICO unique de todo el sistema; `label = f"v{seq}"` |
| label | str | — | |
| kind | str | "batch" | enum KINDS (§12; incluye `ddl` desde el doc 30 y `copy` desde el doc 75 = bloques copiados de otro proyecto al crear) |
| title | str | — | |
| description | str? | null | |
| author | str | — | |
| createdAt / appliedAt | str? | null | |
| status | str | "applied" | `applied` \| `baseline` |
| diff | dict | `{added:[],edited:[],removed:[]}` | listas de strings legibles |
| impact | dict | `{tables:0,columns:0}` | conteo de impacto |
| snapshot | dict | {} | estado COMPLETO tras aplicar: `{domains[], dict[], namingConfig{}, udp[], ddlRules[], ddlConfig{}}` (rollback determinista) |
| revertsSeq | int? | null | si `kind=rollback`: la versión a la que revirtió |

> El snapshot (`snapshot_of` en `data_standards/service.py`, puro) incluye — además de dominios, diccionario y naming — `udp[]` (defs UDP), `ddlRules[]` (las reglas COMPLETAS, con el `validationState` de ese momento) y `ddlConfig{}` (lookups + functions): el rollback restaura estándares y reglas DDL JUNTOS (doc 30 D1). Snapshots anteriores a la feature, sin `ddlRules`/`ddlConfig`, se leen como lista/config vacía.

### `parent_domains` — ParentDomainDoc
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK |
| projectId | str | — | ref `projects.id` (alcance; el mismo nombre de dominio puede tener tipos distintos en dos proyectos) |
| name | str | — | único por proyecto |
| defaultDataType | str | — | tipo que heredan las columnas del dominio (sin override) |
| namingTerm | str? | null | término de naming sugerido por el dominio |
| description | str? | null | |
| logicalDataType | str? | null | doc 69: tipo LÓGICO del dominio (`defaultDataType` es el físico) |
| inheritsName | bool | false | doc 79: dominio «atributo estándar» — al asignarlo, el atributo hereda su nombre y su definición |
| physicalName | str? | null | doc 85: faceta física; `null` = derivado del nombre lógico con el naming del scope `column` (no se persiste); texto = override |
| physicalDescription | str? | null | doc 85: descripción física; `null` = igual a la lógica |
| udpValues | dict[str,str]? | null | doc 85: valores POR DEFECTO de UDP de columna (`{udpDefId: value}`, ambas facetas) que hereda quien asigna el dominio; el reporting y el DDL no los leen. Doc 105 (ronda 5): `standards/apply` los valida por el tipo de su definición (422) |

### `glossary_terms` — AbbreviationDoc  *(diccionario de abreviaturas lógico↔físico)*
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK |
| projectId | str | — | ref `projects.id` (alcance; cambiar un término re-physicaliza SÓLO ese proyecto) |
| term | str | — | palabra lógica |
| abbrev | str | — | abreviatura física |
| scope | str | "column" | `column` \| `table` |
| locked | bool | false | lock por admin (intocable para todos hasta desbloquear). Doc 95 D6: los términos que CREA el one-shot nacen bloqueados (`lockedBy = "one-shot"`); los que crean los modeladores, no |
| lockedBy | str? | null | |
| lockedAt | str? | null | |

### `udp_definitions` — UdpDefinitionDoc  *(User Defined Properties = etiquetas key-value)*

> Doc 61 r2: el catálogo es **FIJO** (`scripts/erwin_migration/standard_udps.py`) — la migración lo siembra completo y mapea los valores del XML contra él (match CI + alias); las defs ya no se derivan del XML. Doc 75 D10: el catálogo se siembra IGUAL en cada proyecto (ids namespaceados por proyecto), así que las 25 defs existen por proyecto con el mismo nombre/nivel/valores.
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK (los `udpValues` de tablas/columnas/canvases usan este `id` como key) |
| projectId | str | — | ref `projects.id` (alcance) |
| name | str | — | la KEY visible (p. ej. `"Clasificación del Dato"`) |
| level | str | "column" | `table` \| `column` \| `canvas` \| `view` (doc 61) |
| dataType | str | "string" | `string` \| `number` \| `boolean` \| `date` \| `list` |
| defaultValue | str? | null | doc 105 (rondas 5–6): validado por `dataType` al aplicar y grabado normalizado (ver abajo) |
| allowedValues | list[str] | [] | enum cuando `dataType='list'` (p. ej. `[DAC, NO DAC, …]`) |
| description | str? | null | |

> **Valores de UDP por tipo (doc 105, ronda 5).** Un valor de UDP se guarda como TEXTO (`udpValues` de tablas, columnas, vistas, canvases y dominios; `defaultValue` de la definición). `standards/apply` valida el `defaultValue` de cada definición del lote y los `udpValues` de los dominios contra el `dataType` —`boolean` normalizado a `true`/`false` (grafías de `app/core/udp_values.py`: `true`, `1`, `sí`, `yes`, `verdadero` y `false`, `0`, `no`, `falso`), `number` finito y en su forma canónica (`canonical_number`: «10.50» se graba «10.5», «1e3» se graba «1000»; ronda 6), `date` ISO `YYYY-MM-DD` real (de una fecha-hora ISO, sólo la fecha; ronda 6) y `list` dentro de `allowedValues` (comparado sin bordes, con los espacios internos colapsados y sin distinguir mayúsculas —`list_key`—; se graba la grafía de la lista)— y responde 422 con el primero inválido (de un dominio, sólo los valores que cambian: ronda 6); la carga Excel aplica las mismas reglas a sus celdas y a los defaults (`invalid-udp-value`), y el motor del Reporting filtra un UDP `boolean` con esas grafías y busca un UDP `number` también por esa forma canónica. Antes se grababa cualquier texto. Los `udpValues` que escribe un cambio de changeset NO se validan por tipo.

### `naming_config` — NamingConfigDoc
**1 doc por (proyecto, scope)**; `_id = "<projectId>:<scope>"` (`scope.naming_id`; no hay campo `id`).
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| scope | str | — | `column` \| `table` (parte de la clave natural) |
| projectId | str | — | ref `projects.id` (parte de la clave natural) |
| separator | str | "" | separador al concatenar términos |
| case | str | "upper" | `upper` \| `lower` \| `camel` |
| maxLength | int | 150 | límite de caracteres del nombre físico; `0` = sin límite (doc 105) |

Si falta un scope en un proyecto, el repo lo siembra con `DEFAULTS` (`separator:"", case:"upper", maxLength:150` para ambos); el kit lo siembra con `$setOnInsert` (no pisa un naming ya editado). Doc 105 (revisión R2): al LEER, un valor guardado que el motor no puede usar (`case` fuera de `upper`/`lower`/`camel`, `maxLength` que no es un entero ≥ 0 —un booleano tampoco—, `separator` que no es texto) cae al default del scope y se registra en el log (`invalid naming config value ignored`; regla `settings.repository._usable`, la misma que usan el rollback de estándares —`restore_naming` graba `null` lo inválido o ausente del snapshot, que al leer es el default— y el kit Erwin, que lee la colección directo); al escribir, `standards/apply` ya los rechaza con 422 y decide si hay cambio comparando contra lo guardado sin enmascarar, así que regrabar el valor visible limpia un inválido guardado.

### `ddl_rules` — DdlRuleDoc  *(DDL Export Rules, doc 30)*
Reglas que transforman el TEXTO SQL del Export DDL según valores de UDP; nunca tocan el modelo ni la data (los artefactos que generan viven solo en el `.sql` exportado). Sin versionado propio: entran al snapshot de `standards_versions` del proyecto y mutan SOLO vía `POST /api/projects/{pid}/standards/apply` (`rulesUpsert`/`rulesDelete`); el router `/api/projects/{pid}/ddl-rules` es de lectura.
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK |
| projectId | str | — | ref `projects.id` (alcance; cada proyecto tiene su ruleset) |
| name | str | — | slug único entre reglas ACTIVAS del proyecto (p. ej. `enmascarar_dac`); la unicidad la garantiza el apply de standards, NO un índice único |
| description | str? | null | |
| kind | str | "rule" | `rule` \| `generator` |
| target | str? | "column" | `column` \| `table`; solo `kind='rule'` (en generators el repo lo fuerza a null) |
| sourceArtifact | str? | null | artefacto de entrada; solo `kind='generator'` (en rules va null) |
| condition | str | "" | DSL SQL-like en forma canónica con corchetes; vacía = la regla aplica siempre |
| udpRefs | list[UdpRef] | [] | binding regla→UDP **por id**, derivado de `condition`/`action` al validar |
| action | dict | {} | una de 4 formas: `expression` \| `tags` \| `tblproperties` \| `emit` |
| appliesTo | list[str] | [] | artefactos destino; solo `kind='rule'` (en generators el repo lo vacía) |
| priority | int | 100 | orden de ejecución `(priority DESC, name ASC)` |
| enabled | bool | true | |
| validationState | str | "valid" | `valid` \| `invalid` \| `stale`; las invalid/stale se guardan igual — el export las salta y las reporta |
| validationReport | dict | {} | resultado del último ciclo de validación (los 5 checks) |
| updatedBy | str? | null | |

**Embebido `UdpRef`**: `{ udpId: str (ref udp_definitions.id), level: str ('table'|'column'|'canvas') }`. El binding por **id** hace que renombrar un UDP no rompa la regla (el nombre se resuelve al renderizar); borrar un UDP referenciado por reglas activas se bloquea (409 en el apply).

El repositorio persiste además `flgactive`/`createdAt`/`updatedAt`/`deletedAt` fuera del modelo (se descartan al leer; patrón §0). Artefactos RAÍZ del export (siempre existen): `ddl.tabla_fisica` y `ddl.vista_negocio`; los generadores declaran los suyos vía `action.emit.artifact` — el catálogo es raíces + declarados, nunca un enum cerrado.

### `ddl_ruleset_config` — DdlRulesetConfigDoc  *(un doc por proyecto, `_id = projectId`)*
Config del ruleset DEL proyecto: lookups (mapeo valor-de-UDP → valor emitido, con default) y funciones reusables.
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | — | PK (`_id` == `projectId`); 1 doc por proyecto |
| projectId | str | — | ref `projects.id` |
| lookups | dict | {} | `{nombre: {fromUdpId (ref udp_definitions.id), fromLevel, values: {valorUdp: emitido o null}, default: str o null}}` |
| functions | list | [] | `[{name, params: [str], body: str}]` |

`ddlConfigPatch` del apply manda el set COMPLETO de lookups/functions (no deltas); si el doc no existe, la lectura devuelve el default vacío.

---

## 8. Identidad y RBAC

### `users` — UserDoc  *(`_id = username`)*

Doc 38: la colección funciona como **WHITELIST de acceso** — las entradas normales son correos asignados a un rol (`_id` = correo lowercase, sin `passwordHash`) que entran por el SSO de Databricks; las cuentas locales con contraseña (`admin`) conviven en la misma colección. `name`/`initials`/`email` de una entrada SSO los completa el primer login (SCIM / derivado del correo) si están vacíos.

| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | — | PK = **username** (correo lowercase en entradas SSO) |
| email | str | "" | en entradas SSO se autocompleta con el propio correo |
| name | str | "" | vacío hasta el primer login SSO (o lo fija el admin) |
| role | str | "" | ref `roles.id` (key del rol) |
| projectIds | list[str] | [] | reservado para el acceso por proyecto (sin enforcement todavía); `[]`/ausente = todos. La cascada de borrado de un proyecto hace `$pull` de su id |
| status | str | "active" | `active` \| `invited` \| `disabled` |
| initials | str? | null | |
| hasPassword | bool | false | **DERIVADO en lectura** (`_to_user`), no se persiste: true solo en cuentas locales |
| passwordHash | str? | null | bcrypt; solo cuentas locales; **NUNCA se expone** (el service hace `exclude`) |

### `roles` — RoleDoc  *(`_id = key`)*
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | — | PK = **key** (`administrador` \| `modelador` \| `revisor` \| `lector` \| …) |
| name | str | — | etiqueta visible |
| description | str? | null | |
| permissions | dict[str,bool] | {} | matriz `permiso → bool` (catálogo PERMISSIONS, §12) |

---

## 9. Otros (sin modelo `*Doc` en `models/`)

### `saved_reports` — SavedReportDoc  *(en `reporting/query/reports.py`)*
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK |
| projectId | str | — | ref `projects.id` (doc 75 D13: un reporte consulta UN proyecto; igual al `spec.projectId`) |
| name | str | — | |
| description | str? | null | |
| spec | dict | — | **QuerySpec** serializado (IR del reporting; `spec.projectId` obligatorio) |
| shared | bool | false | visible para otros usuarios |
| folderId | str? | null | carpeta de reportes (organización) |
| owner | str | — | ref `users.id` (fijado server-side) |
| createdAt / updatedAt | str? | null | |

### `sheet_templates` — SheetTemplateDoc  *(en `reporting/sheet_templates/models.py`, doc 95 D11)*
Formato Excel FIJO del Reporting guardado como dato (se exporta desde el front con las funciones puras del export tabular).

| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK |
| projectId | str | — | ref `projects.id` (alcance; índice `projectId`) |
| name | str | — | 1–80; único (CI) entre las que ve quien escribe (las suyas + las compartidas) |
| sheetName | str | — | nombre de la hoja: 1–31, sin `: \ / ? * [ ]`. Doc 105: al ESCRIBIR el tope se cuenta en unidades UTF-16, como Excel y SheetJS; al LEER no rige (una plantilla guardada antes con ≤ 31 code points —p. ej. con un emoji— no deja en 500 el listado del proyecto y se puede abrir para corregirla) |
| description | str? | null | |
| columns | list[{header, source}] | — | 1–200; `header` único (CI); `source` = `table.<campo>` · `column.<campo>` · `table.udp:<nombre>` · `column.udp:<nombre>` |
| shared | bool | false | compartida con el proyecto (spec D11: «dueño + compartida, como los saved reports»); la sembrada nace compartida |
| owner | str? | null | dueño (lo fija el servidor): edita y borra; un admin, además, las compartidas. La del one-shot: `system` |
| origin | str | "user" | `user` \| `builtin:qa-modelo` (la «QA_MODELO» sembrada por el one-shot o el botón) |
| createdBy / updatedBy | str? | null | |
| createdAt / updatedAt | str? | null | + `flgactive`/`deletedAt` (soft-delete) |

### `upload_jobs`  *(en `bulk_upload/jobs.py`, doc 105 — sin modelo `*Doc`)*
Estado de cada job de la carga masiva desde Excel. Vive en la BD (antes en la memoria del proceso) porque con `uvicorn --workers 2` el POST que crea el job, el polling, el apply y el descarte pueden caer en procesos distintos; sólo la task que valida o aplica es del proceso que la lanzó.

| Campo | Tipo | Notas |
|---|---|---|
| _id | str | `uuid4().hex` |
| csId | str | ref `changesets.id` (la versión donde se carga) |
| owner | str | ref `users.id` (índice `owner`: tope de 20 jobs por usuario y desalojo) |
| fileName | str | nombre del workbook |
| status | str | `validating` \| `validated` \| `applying` \| `applied` \| `failed` |
| progress | dict | `{phase, done, total}`; cada escritura de avance es también su latido |
| report / result / error | dict? / dict? / str? | reporte de validación, resultado del apply (`{affectedCanvasIds, counts}`), motivo de falla |
| createdAt / updatedAt | float | **epoch** (segundos), no ISO — a diferencia del resto |

Un job activo sin avance en 10 min es de un proceso que murió: se informa `failed` («The upload stopped unexpectedly…») y se puede descartar. Los terminados se desalojan (borrado físico, sin soft-delete) cuando pasaron 30 min desde su última actualización — el desalojo corre al crear un job nuevo. Robustez con dos procesos (doc 105, revisión R1): el desalojo y el tope por usuario borran con el predicado en la MISMA sentencia (un job que otro proceso reclamó en el medio ya no cumple y no se va); el descarte vuelve a leer si el estado cambió entre su lectura y su borrado (hasta 3 veces; si sigue cambiando, «busy»); y el alta inserta el job ANTES que su cuerpo (al revés, una caída en el medio dejaba un cuerpo que ningún desalojo encontraba; si el cuerpo falla, el job se retira). El lock de «un apply por versión» NO vive acá: es `changesets.uploadLock`.

### `upload_job_bodies`  *(en `bulk_upload/jobs.py`, doc 105)*
`{ _id (= _id del job), body (el UploadWorkbookBody: hojas crudas + profileId), createdAt (epoch) }`. Lo lee el apply —en el proceso que lo tome— para re-validar; se borra cuando ya no sirve (al terminar un job que no quedó `validated`, al descartarlo o al desalojarlo); el desalojo barre además los cuerpos sin job de más de 40 min (el job se borra antes que su cuerpo: si lo segundo fallaba, el cuerpo quedaba huérfano), a lo más una vez cada 30 min por proceso (`SWEEP_EVERY_SECONDS`, ronda 4: leer los cuerpos costaba en cada carga).

### `deleted_changesets`  *(en `changesets/repository.py`, doc 105 — lápidas)*
`{ _id (= "<csId>:<uuid>"), csId (el changeset que se elimina), at (epoch) }` — una por INTENTO de eliminación, así dos intentos en paralelo no se pisan. `delete_changeset` la escribe ANTES de borrar la cabecera y retira sólo la suya al terminar (o si la condición del borrado ya no se cumplía). Si el proceso muere entre la cabecera y sus cambios, la lápida queda. La purga del arranque (`purge_orphan_changes`) agrupa las lápidas por `csId` (una legada sin `csId` usa su `_id`): si la cabecera ya no existe, borra ya los cambios de esa versión y retira sus lápidas; si sigue viva, sólo retira las leídas de más de 10 min (`TOMBSTONE_GRACE_SECONDS`). Sin índices propios.

### `audit_log`  *(append-only, `_id` = ObjectId auto)*
Shape (de `core/audit.py`): `{ at: str(ISO), actor: str, action: str, target?: str, targetType?: str, meta?: dict }`. Sin soft-delete.

---

## 10. Tipo embebido transversal

### `TagDoc` (`app/core/models.py`) — embebido, sin colección propia
`{ key: str = "", value: str = "" }`. El helper `coerce_tags` acepta la forma legacy `list[str]` y la normaliza a `{key:"", value:<str>}` (strings vacíos se descartan).

---

## 11. Referencias entre colecciones (todas por STRING, sin FK)

| Desde | Campo | Apunta a | Por |
|---|---|---|---|
| (todas las de `PROJECT_SCOPED`) | projectId | projects.id | id |
| folders | projectId | projects.id | id |
| folders | parentFolderId | folders.id | id |
| subject_areas | projectId | projects.id | id |
| subject_areas | folderId | folders.id | id |
| subject_areas | tableIds[] | canonical_tables.id | id |
| subject_areas | layout (keys) | canonical_tables.id / views.id | id |
| canonical_tables | schema | schemas.name | **nombre** |
| canonical_columns | tableId | canonical_tables.id | id |
| canonical_columns | parentDomainId | parent_domains.id | id |
| relationships | parentTableId / childTableId | canonical_tables.id | id |
| relationships | pairs[].parentColumnId / childColumnId | canonical_columns.id | id |
| views | schema | schemas.name | **nombre** |
| views | tableId / sourceTableIds[] | canonical_tables.id | id |
| views | sources[].tableId | canonical_tables.id | id |
| views | sources[].column | canonical_columns.physicalName | **nombre** |
| changeset_changes | csId | changesets.id | id |
| changeset_changes | entityId | (id en la colección `collection`) | id |
| changesets | projectId | projects.id | id |
| changesets | owner / reviewers[] | users.id | username |
| projects | deletedIn (fuera del modelo) | changesets.id | id |
| users | role | roles.id | key |
| saved_reports | owner | users.id | username |
| upload_jobs | csId | changesets.id | id |
| upload_jobs | owner | users.id | username |
| upload_job_bodies | _id | upload_jobs._id | id |
| changesets | uploadLock.jobId | upload_jobs._id | id |
| deleted_changesets | csId (y el prefijo de `_id`) | changesets.id (ya eliminado) | id |
| audit_log | actor | users.id | username |
| ddl_rules | udpRefs[].udpId | udp_definitions.id | id |
| ddl_ruleset_config | lookups.*.fromUdpId | udp_definitions.id | id |
| *.udpValues (keys) | — | udp_definitions.id | id |

---

## 12. Catálogos de valores (enums del código)

- **Cardinalidad de relación** (`relationships/models.py CARDINALITIES`): `one`, `many`, `one-only`, `zero-one`, `one-many`, `zero-many`. (`_ONEISH = {one, zero-one, one-only}`, `_MANYISH = {many, one-many, zero-many}`.)
- **Permisos RBAC** (`auth/models.py PERMISSIONS`): `model.view`, `model.edit`, `review.decide`, `publish`, `rollback`, `export`, `standards.edit`, `admin.manage`. `ACCESS_LEVELS = (full, edit, read)`.
- **UDP** (`udp/models.py`): `UDP_TYPES = (string, number, boolean, date, list)`, `UDP_LEVELS = (table, column, canvas, view)`.
- **Standards version kind** (`data_standards/models.py KINDS`): `glossary`, `udp`, `domain`, `naming`, `ddl`, `batch`, `baseline`, `rollback`, `copy`.
- **Bloques copiables de estándares** (`data_standards/schemas.py CopyFromBody.blocks`): `glossary`, `domains`, `udp`, `naming`, `ddl`.
- **Colecciones con alcance** (`core/scope.py PROJECT_SCOPED`): `folders`, `subject_areas`, `schemas`, `canonical_tables`, `canonical_columns`, `relationships`, `views`, `parent_domains`, `glossary_terms`, `udp_definitions`, `naming_config`, `ddl_rules`, `ddl_ruleset_config`, `upload_profiles`, `sheet_templates`, `changesets`, `standards_versions`, `saved_reports`.
- **Reglas DDL** (`ddl_rules/models.py`): `RULE_KINDS = (rule, generator)`, `RULE_TARGETS = (column, table)`, `VALIDATION_STATES = (valid, invalid, stale)`.
- **Estado de changeset**: `draft`, `submitted`, `approved`, `rejected`.
- **Estado de usuario**: `active`, `invited`, `disabled`.
- **naming case**: `upper`, `lower`, `camel`. **naming scope**: `column`, `table`.

---

## 13. Hechos del estado actual relevantes para una migración de esquema

Solo **descripción del estado actual** (no recomendaciones de destino):

1. **Estructuras embebidas / denormalizadas** (viven dentro de un doc, sin sub-colección): `udpValues` (map en tables/columns/subject_areas/views), `layout` (map → `{x,y}`), `drawings` (list) y `routes` (map → lista de `{x,y}`, doc 99) en `subject_areas`, `pairs` en `relationships`, `sources` en `views`, `approvals`/`comments` en `changesets`, `snapshot`/`diff`/`impact` en `standards_versions`, `permissions` en `roles`, `spec` (QuerySpec) en `saved_reports`, `udpRefs`/`action` en `ddl_rules`, `lookups`/`functions` en `ddl_ruleset_config`.
2. **Referencias sin integridad referencial**: todo apunta por string (id o **nombre** — `schema` y `sources[].column` son por nombre). Nada lo valida el almacén.
3. **Soft-delete** por `flgactive:false` + `deletedAt` en casi todo; `audit_log` es append-only; `changesets`/`changeset_changes`/`standards_versions` no usan `flgactive`.
4. **`_id` especiales**: `changeset_changes._id` es DETERMINISTA (`{csId}::{collection}::{entityId}`); `naming_config._id = "<projectId>:<scope>"`; `users._id = username`; `roles._id = key`; `ddl_ruleset_config._id = projectId`; `audit_log._id` = ObjectId auto. El resto = `id` uuid4 (los migrados desde Erwin: `uuid5("<projectId>|<Long_Id>")`, doc 75 D11 — el mismo objeto Erwin cargado en dos proyectos tiene ids distintos).
5. **Alias `schema`**: en `canonical_tables` y `views` el atributo Python es `sql_schema` pero el campo persistido es `schema`.
6. **Campos legacy/aditivos fuera del modelo**: por `extra="ignore"`, los docs persistidos pueden traer `migratedFrom`, `erwinLongId`, `flgactive`, `deletedAt`, `deletedIn` (cascada de borrado de proyecto), y (relaciones pre-v2) `sourceTableId`/`targetTableId`. **Están en la BD aunque el modelo no los liste** — mirar el doc real al migrar.
7. **Colecciones sin `*Doc`**: `saved_reports` (modelo en `reporting/query/reports.py`), `audit_log` (shape en `core/audit.py`) y — doc 105 — `upload_jobs` / `upload_job_bodies` (shape en `bulk_upload/jobs.py`; timestamps epoch; borrado físico por TTL) y `deleted_changesets` (lápidas `{_id: "<csId>:<uuid>", csId, at}`, `changesets/repository.py`).
8. **Retiradas**: `column_catalog` (del planteamiento inicial con agente conversacional, descartado — doc 54): ya no se pre-crea ni se preserva; el reset destructivo la elimina.
9. **Foto de la data**: la BD vigente (cargada antes del doc 75, 2026-09-07) tiene los 4 XML de `folder_data/` en 2 proyectos y SIN `projectId` en los docs de estándares — es incompatible con este esquema y se reemplaza con el one-shot `scripts/run_migration.py --folder ../folder_data --apply --force` (destructivo), que deja **3 proyectos** según la convención del doc 77 (subcarpeta = un proyecto; `.xml` suelto = un proyecto): «MODELO DDV» (la carpeta con CPYBCA + Otros), «UDV INT FISICO» y «UDV INT LOGICO» (un archivo cada uno), cada uno con sus estándares (unión distinta de sus archivos), su ruleset DDL base y su `v1`.
10. **Versiones vigentes (por proyecto)**: cada proyecto tiene su `v1 Base` — un changeset MARCADOR (status `approved`, `appliedAt` estampado, 0 docs en `changeset_changes`, `projectId` del proyecto) creado por `scripts/mark_base_version.py` (o por `POST /api/projects` para un proyecto nuevo); sin él la web bloquea el módulo Model para ese proyecto. Las cargas de migración Erwin escriben DIRECTO a las colecciones publicadas, sin crear changesets. Data Standards de cada proyecto tiene `v1` (baseline) + `v2` "Base — DDL export rules"; los estándares vivos DERIVARON de esos snapshots — no hacer rollback de Standards de un proyecto hasta registrar una baseline nueva en ese proyecto.

---

## Ver también
- `arquitectura.md` §7 — visión de colecciones, erDiagram e índices.
- `consideraciones-y-limites.md` §4.6 — lista completa de índices (`ensure_indexes`).
- `migracion-erwin.md` — cómo se puebla este esquema desde un XML de Erwin.
