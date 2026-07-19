# Esquema de datos — Data Model Hub (referencia completa de colecciones)

**Actualizado:** 2026-07-19 · Fuente: los modelos Pydantic `*Doc` reales de `app/features/*/models.py` + `app/core/models.py`, los `repository.py` (nombres de colección) y `app/core/db/indexes.py` (índices).

Referencia **campo por campo** de todas las colecciones que administra este backend en Azure Cosmos DB (API de Mongo, base `db_modeler`). Pensada como insumo para diseñar/migrar el esquema (p. ej. a un modelo relacional). Describe el **estado actual**, no cómo migrar. Complementa `arquitectura.md` (§7, visión) y `migracion-erwin.md` (carga desde XML de Erwin).

---

## 0. Convenciones globales (aplican a TODA colección)

- **`model_config = DOC_CONFIG`** (`app/core/models.py`): `ConfigDict(extra="ignore", populate_by_name=True)`.
  - `extra="ignore"` ⇒ al **leer**, cualquier campo del doc de Mongo que NO esté declarado en el `*Doc` se **descarta silenciosamente** (no llega a la app). Implicación de migración: para no perder datos hay que mirar el **doc real en Mongo**, no solo el modelo — puede haber campos legacy o aditivos (p. ej. `migratedFrom`, `erwinLongId`, `flgactive`, `deletedAt`) que persisten en Mongo pero el modelo ignora.
  - `populate_by_name=True` ⇒ los campos con `alias` aceptan el nombre del atributo o el alias en la entrada.
- **`_id` vs `id`**: el repositorio guarda el `id` (o la clave natural) del modelo como `_id` de Mongo al insertar, y lo revierte a `id` al leer. Donde el `_id` es especial se anota en cada colección.
- **Soft-delete**: la mayoría **no borra**; marca `flgactive: false` + `deletedAt` (ISO) y las lecturas filtran `flgactive != false`. Excepciones: `audit_log` es append-only; `changesets` / `changeset_changes` / `standards_versions` no usan `flgactive` (se gobiernan por estado/seq).
- **Sin integridad referencial**: todas las referencias entre colecciones son **strings** (un `id`, o a veces un **nombre**). Mongo no las valida — la consistencia la garantiza la app. Ver §Referencias (final).
- **Timestamps**: strings ISO-8601 (`datetime.now(timezone.utc).isoformat()`), no `Date` de Mongo.
- **Trazabilidad de migración Erwin**: los docs cargados desde XML llevan además `migratedFrom: "erwin"` + `erwinLongId` (no declarados en el modelo → se ignoran al leer, pero **persisten** en Mongo).

**Notación de las tablas de campos:** `str?` = opcional/nullable; `dict[str,str]` = mapa embebido; “ref X.y” = referencia por string a la colección X campo y.

---

## 1. Mapa de colecciones (19 propias)

| Colección | Modelo `*Doc` | Versionada (changeset) | Soft-delete | `_id` |
|---|---|:--:|:--:|---|
| `projects` | ProjectDoc | ✅ | ✅ | `id` (uuid) |
| `folders` | FolderDoc | ✅ | ✅ | `id` (uuid) |
| `subject_areas` (canvases) | SubjectAreaDoc | ✅ | ✅ | `id` (uuid) |
| `schemas` | SchemaDoc | ✅ | ✅ | `id` (`sch-<name>` en migración) |
| `canonical_tables` | CanonicalTableDoc | ✅ | ✅ | `id` (uuid) |
| `canonical_columns` | CanonicalColumnDoc | ✅ | ✅ | `id` (uuid) |
| `relationships` | RelationshipDoc | ✅ | ✅ | `id` (uuid) |
| `views` | ViewDoc | ✅ | ✅ | `id` (uuid) |
| `changesets` | ChangesetDoc | — (es el mecanismo) | — | `id` (uuid) |
| `changeset_changes` | ChangeDoc | — | — | **determinista** `{csId}::{collection}::{entityId}` |
| `standards_versions` | StandardsVersionDoc | — (versionado propio) | — | `id` (uuid); único por `seq` |
| `parent_domains` | ParentDomainDoc | — (Data Standards) | ✅ | `id` (uuid) |
| `glossary_terms` | AbbreviationDoc | — (Data Standards) | ✅ | `id` (uuid) |
| `udp_definitions` | UdpDefinitionDoc | — (Data Standards) | ✅ | `id` (uuid) |
| `naming_config` | NamingConfigDoc | — (Data Standards) | — | **`scope`** (clave natural; NO hay campo `id`) |
| `users` | UserDoc | — | via `status=disabled` | **`username`** |
| `roles` | RoleDoc | — | — | **`key`** (slug del rol) |
| `saved_reports` | SavedReportDoc (en `reporting/query/reports.py`, no en `models/`) | — | ✅ | `id` (uuid) |
| `audit_log` | (sin modelo) | — | append-only | `ObjectId` auto |

> **`column_catalog` NO es de este backend.** Lo administra el servicio de agentes (`app-agents-modeler`) sobre la MISMA base; los scripts (`reset_for_migration`, `seed_*`) lo **preservan**. No confundir con `canonical_columns` (el store real de columnas de esta app).
>
> **Entidades virtuales del reporting** (no son colecciones): `COLL_OF` en `reporting/query/executor.py` mapea `view_columns → views` y `models → subject_areas`.

---

## 2. Alcance del versionado

**`VERSIONED`** (`changesets/repository.py`, en orden de dependencia del apply): las 8 colecciones cuyos cambios pasan por el changeset (draft → submit → review → approve → publish):

```
projects · folders · subject_areas · schemas · canonical_tables · canonical_columns · relationships · views
```

Un cambio no toca la colección publicada hasta el **apply del approve**: vive como un doc en `changeset_changes` con el **doc completo** en `payload`. El `overlay(publicado, cambios)` produce el estado efectivo del draft.

**Data Standards** se versiona **aparte** (no por el changeset): `parent_domains`, `glossary_terms`, `udp_definitions` y `naming_config` se escriben directo a producción y cada apply/rollback deja una versión con **snapshot completo** en `standards_versions`.

**No versionado**: `users`, `roles`, `saved_reports`, `audit_log`, `changesets`, `changeset_changes`.

---

## 3. Estructura (Model Explorer)

### `projects` — ProjectDoc
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK (`_id`) |
| name | str | — | |
| description | str? | null | |

### `folders` — FolderDoc
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK |
| projectId | str | — | ref `projects.id` |
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

**Embebido `NodePosDoc`**: `{ x: float, y: float }`.

### `schemas` — SchemaDoc
Esquema físico de BD como **entidad** (doc 18). Tablas y vistas lo referencian por **nombre** (string), no por id.
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK (migración: `sch-<name>`, reusa por nombre case-insensitive) |
| name | str | — | único (case-insensitive) |
| description | str? | null | |

---

## 4. Catálogo canónico (pool universal, no atado a proyecto)

### `canonical_tables` — CanonicalTableDoc
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK |
| physicalName | str | — | nombre físico (DDL) |
| logicalName | str | — | nombre lógico (negocio) |
| **schema** | str? | null | **alias** de `sql_schema`; el nombre del esquema (ref `schemas.name` **por nombre**) |
| description | str? | null | definición funcional |
| udpValues | dict[str,str] | {} | `{udpDefId: value}` (valores de las etiquetas UDP nivel table) |

### `canonical_columns` — CanonicalColumnDoc
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK |
| tableId | str | — | ref `canonical_tables.id` |
| physicalName | str | — | |
| logicalName | str | — | |
| parentDomainId | str? | null | ref `parent_domains.id` |
| dataType | str | — | tipo físico |
| typeOverridden | bool | false | true = `dataType` es override manual (no hereda del dominio) |
| isPrimaryKey | bool? | null | |
| pkPosition | int? | null | orden **0-based dentro de la PK** (≠ `ordinal` físico; ver doc 19 §12b) |
| isForeignKey | bool? | null | |
| isNullable | bool | true | |
| isPartition | bool | false | columna de partición (DDL emite `PARTITIONED BY`) |
| description | str? | null | definición funcional a nivel columna |
| ordinal | int | 0 | **orden físico** de la columna en la tabla |
| udpValues | dict[str,str] | {} | `{udpDefId: value}` |

> Dos órdenes distintos y **ambos** persistidos: `ordinal` (físico de columnas) y `pkPosition` (orden de la llave). Erwin/DDL usan el de la llave para `PRIMARY KEY(...)`.

---

## 5. Relaciones y vistas

### `relationships` — RelationshipDoc
Un doc por relación Erwin con **todos** sus pares de columnas (FK compuesta = varios `pairs`).
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK |
| parentTableId | str | — | lado PK (“one”); ref `canonical_tables.id` |
| childTableId | str | — | lado FK (“many”); ref `canonical_tables.id` |
| pairs | list[RelationshipPairDoc] | (min 1) | pares de columnas PK↔FK |
| parentCardinality | str | "one" | enum CARDINALITIES (ver §12) |
| childCardinality | str | "zero-many" | enum CARDINALITIES |
| identifying | bool | false | relación **sólida**: la FK es parte de la PK del hijo |

**Embebido `RelationshipPairDoc`**: `{ parentColumnId: str (ref canonical_columns.id), childColumnId: str (ref canonical_columns.id), roleName: str? }`. (`roleName` = rolename estilo Erwin; el nombre real de la columna vive en el hijo.)

> Docs legacy v1 (`sourceTableId`/`targetTableId`, un solo par) se **normalizan a v2** al leer (`_upgrade_legacy` → `parent/child` + `pairs`).

### `views` — ViewDoc
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK |
| name | str | — | |
| sql | str | "" | `CREATE VIEW` original (referencia congelada; el Export DDL regenera desde `sources`) |
| description | str? | null | definición funcional a nivel **vista** (F5) |
| tableId | str? | null | compat = `sourceTableIds[0]` |
| **schema** | str? | null | alias de `sql_schema` (ref `schemas.name` por nombre) |
| tags | list[str] | [] | |
| filter | str? | null | cláusula WHERE |
| sources | list[dict] | [] | proyección **columna a columna** (ver shape abajo); list[dict] sin sub-schema estricto |
| outputAlias | str? | null | |
| expression | str? | null | |
| sourceTableIds | list[str] | [] | refs `canonical_tables.id`; el **orden** define los alias `t1, t2…` |
| showOnCanvas | bool | false | flag GLOBAL: la vista aparece en todo canvas con ≥1 fuente presente |
| joinOverride | str? | null | condición JOIN manual (la consume el DDL del front; el backend no la valida) |

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
| versionLabel | str? | null | p. ej. `"v15"` (autoincremental) |
| projectIds | list[str] | [] | cross-project (chips); refs `projects.id` |
| reviewers | list[str] | [] | userIds asignados |
| approvals | dict[str,dict] | {} | `{userId: {status:'approved'|'rejected', note?, at}}` |
| comments | list[dict] | [] | `{author, text, at}` |
| createdAt / updatedAt / submittedAt / reviewedBy / reviewedAt / reviewNote | str? | null | timestamps + compat de revisión single |
| appliedAt | str? | null | timestamp del apply EXITOSO a producción (`approved` sin `appliedAt` = murió a mitad; recuperable con `scripts/reapply_changeset.py`) |

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
| before | dict? | null | imagen PREVIA de la entidad publicada (alimenta el rollback = draft inverso) |
| beforeAt | str? | null | `before=null` + `beforeAt` estampado = la entidad no existía (inverso = delete) |

---

## 7. Data Standards (glosario, dominios, UDP, naming) — versionado propio

### `standards_versions` — StandardsVersionDoc
Historial append-only; cada apply/rollback = una versión con snapshot completo.
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | — | PK (uuid) |
| seq | int | — | monotónico; **índice único**; `label = f"v{seq}"` |
| label | str | — | |
| kind | str | "batch" | enum KINDS (§12) |
| title | str | — | |
| description | str? | null | |
| author | str | — | |
| createdAt / appliedAt | str? | null | |
| status | str | "applied" | `applied` \| `baseline` |
| diff | dict | `{added:[],edited:[],removed:[]}` | listas de strings legibles |
| impact | dict | `{tables:0,columns:0}` | conteo de impacto |
| snapshot | dict | {} | estado COMPLETO tras aplicar: `{domains[], dict[], namingConfig{}}` (rollback determinista) |
| revertsSeq | int? | null | si `kind=rollback`: la versión a la que revirtió |

### `parent_domains` — ParentDomainDoc
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK |
| name | str | — | |
| defaultDataType | str | — | tipo que heredan las columnas del dominio (sin override) |
| namingTerm | str? | null | término de naming sugerido por el dominio |
| description | str? | null | |

### `glossary_terms` — AbbreviationDoc  *(diccionario de abreviaturas lógico↔físico)*
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK |
| term | str | — | palabra lógica |
| abbrev | str | — | abreviatura física |
| scope | str | "column" | `column` \| `table` |
| wordType | str? | null | `prime` \| `class` \| `modifier` |
| locked | bool | false | lock por admin (intocable para todos hasta desbloquear) |
| lockedBy | str? | null | |
| lockedAt | str? | null | |

### `udp_definitions` — UdpDefinitionDoc  *(User Defined Properties = etiquetas key-value)*
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK (los `udpValues` de tablas/columnas/canvases usan este `id` como key) |
| name | str | — | la KEY visible (p. ej. `"Clasificación del Dato"`) |
| level | str | "column" | `table` \| `column` \| `canvas` |
| dataType | str | "string" | `string` \| `number` \| `boolean` \| `date` \| `list` |
| defaultValue | str? | null | |
| allowedValues | list[str] | [] | enum cuando `dataType='list'` (p. ej. `[DAC, NO DAC, …]`) |
| description | str? | null | |

### `naming_config` — NamingConfigDoc
**1 doc por scope**; `_id = scope` (no hay campo `id`).
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| scope | str | — | PK (`_id`): `column` \| `table` |
| separator | str | "" | separador al concatenar términos |
| case | str | "upper" | `upper` \| `lower` \| `camel` |
| maxLength | int | 150 | límite de caracteres del nombre físico |

Si falta un scope, el repo lo siembra con `DEFAULTS` (`separator:"", case:"upper", maxLength:150` para ambos).

---

## 8. Identidad y RBAC

### `users` — UserDoc  *(`_id = username`)*
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | — | PK = **username** |
| email | str | "" | |
| name | str | "" | |
| role | str | "" | ref `roles.id` (key del rol) |
| projectIds | list[str] | [] | scope por proyecto; `[]`/ausente = todos (admin) |
| status | str | "active" | `active` \| `invited` \| `disabled` |
| initials | str? | null | |
| passwordHash | str? | null | bcrypt; **NUNCA se expone** (el service hace `exclude`) |

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
| name | str | — | |
| description | str? | null | |
| spec | dict | — | **QuerySpec** serializado (IR del reporting) |
| shared | bool | false | visible para otros usuarios |
| folderId | str? | null | carpeta de reportes (organización) |
| owner | str | — | ref `users.id` (fijado server-side) |
| createdAt / updatedAt | str? | null | |

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
| changesets | owner / reviewers[] | users.id | username |
| users | role | roles.id | key |
| saved_reports | owner | users.id | username |
| audit_log | actor | users.id | username |
| *.udpValues (keys) | — | udp_definitions.id | id |

---

## 12. Catálogos de valores (enums del código)

- **Cardinalidad de relación** (`relationships/models.py CARDINALITIES`): `one`, `many`, `one-only`, `zero-one`, `one-many`, `zero-many`. (`_ONEISH = {one, zero-one, one-only}`, `_MANYISH = {many, one-many, zero-many}`.)
- **Permisos RBAC** (`auth/models.py PERMISSIONS`): `model.view`, `model.edit`, `review.decide`, `publish`, `export`, `standards.edit`, `admin.manage`. `ACCESS_LEVELS = (full, edit, read)`.
- **UDP** (`udp/models.py`): `UDP_TYPES = (string, number, boolean, date, list)`, `UDP_LEVELS = (table, column, canvas)`.
- **Standards version kind** (`data_standards/models.py KINDS`): `glossary`, `udp`, `domain`, `naming`, `batch`, `baseline`, `rollback`.
- **Estado de changeset**: `draft`, `submitted`, `approved`, `rejected`.
- **Estado de usuario**: `active`, `invited`, `disabled`.
- **naming case**: `upper`, `lower`, `camel`. **naming scope**: `column`, `table`.

---

## 13. Hechos del estado actual relevantes para una migración de esquema

Solo **descripción del estado actual** (no recomendaciones de destino):

1. **Estructuras embebidas / denormalizadas** (viven dentro de un doc, sin sub-colección): `udpValues` (map en tables/columns/subject_areas), `layout` (map → `{x,y}`) y `drawings` (list) en `subject_areas`, `pairs` en `relationships`, `sources` en `views`, `approvals`/`comments` en `changesets`, `snapshot`/`diff`/`impact` en `standards_versions`, `permissions` en `roles`, `spec` (QuerySpec) en `saved_reports`.
2. **Referencias sin integridad referencial**: todo apunta por string (id o **nombre** — `schema` y `sources[].column` son por nombre). Nada lo valida Mongo.
3. **Soft-delete** por `flgactive:false` + `deletedAt` en casi todo; `audit_log` es append-only; `changesets`/`changeset_changes`/`standards_versions` no usan `flgactive`.
4. **`_id` especiales**: `changeset_changes._id` es DETERMINISTA (`{csId}::{collection}::{entityId}`); `naming_config._id = scope`; `users._id = username`; `roles._id = key`; `audit_log._id` = ObjectId auto. El resto = `id` uuid4.
5. **Alias `schema`**: en `canonical_tables` y `views` el atributo Python es `sql_schema` pero el campo en Mongo es `schema`.
6. **Campos legacy/aditivos fuera del modelo**: por `extra="ignore"`, docs de Mongo pueden traer `migratedFrom`, `erwinLongId`, `flgactive`, `deletedAt`, y (relaciones pre-v2) `sourceTableId`/`targetTableId`. **Están en Mongo aunque el modelo no los liste** — mirar el doc real al migrar.
7. **Colecciones sin `*Doc`**: `saved_reports` (modelo en `reporting/query/reports.py`) y `audit_log` (shape en `core/audit.py`).
8. **Ajenas a este backend**: `column_catalog` (servicio de agentes) — se preserva, no se administra acá.

---

## Ver también
- `arquitectura.md` §7 — visión de colecciones, erDiagram e índices.
- `consideraciones-y-limites.md` §4.6 — lista completa de índices (`ensure_indexes`).
- `migracion-erwin.md` — cómo se puebla este esquema desde un XML de Erwin.
