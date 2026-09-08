# Esquema de datos — Data Model Hub (referencia completa de colecciones)

**Actualizado:** 2026-09-08 (doc 75: proyectos independientes) · Fuente: los modelos Pydantic `*Doc` reales de `app/features/*/models.py` + `app/core/models.py`, los `repository.py` (nombres de colección) y `app/core/db/indexes.py` (índices).

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
- **Soft-delete**: la mayoría **no borra**; marca `flgactive: false` + `deletedAt` (ISO) y las lecturas filtran `flgactive != false`. Excepciones: `audit_log` es append-only; `changesets` / `changeset_changes` / `standards_versions` no usan `flgactive` (se gobiernan por estado/seq).
- **Alcance por proyecto (doc 75 D1)**: todo documento que describe el modelo o sus estándares pertenece a exactamente UN proyecto y lleva `projectId` (ref `projects.id`), estampado SIEMPRE server-side (nunca lo elige el cliente). `app/core/scope.py` es la única forma de armar un filtro de alcance (`scoped(pid, flt)`); `PROJECT_SCOPED` enumera las 16 colecciones con `projectId` y `published()` del ledger rechaza (`MissingProjectError` → 500, bug de programación) una lectura de esas colecciones sin `projectId`/`_id`/`tableId` en el filtro. Sólo `projects` (la raíz del alcance), `users`, `roles`, `changeset_changes` y `audit_log` no lo llevan.
- **Sin integridad referencial**: todas las referencias entre colecciones son **strings** (un `id`, o a veces un **nombre**). El almacén no las valida (el `doc` jsonb es opaco para Postgres) — la consistencia la garantiza la app. Ver §Referencias (final).
- **Timestamps**: strings ISO-8601 (`datetime.now(timezone.utc).isoformat()`), nunca tipos fecha nativos del almacén (`timestamptz`).
- **Trazabilidad de migración Erwin**: los docs cargados desde XML llevan además `migratedFrom: "erwin"` + `erwinLongId` (no declarados en el modelo → se ignoran al leer, pero **persisten** en el doc).

**Notación de las tablas de campos:** `str?` = opcional/nullable; `dict[str,str]` = mapa embebido; “ref X.y” = referencia por string a la colección X campo y.

---

## 1. Mapa de colecciones (21 propias)

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
| `users` | UserDoc | — | — | via `status=disabled` | **`username`** |
| `roles` | RoleDoc | — | — | — | **`key`** (slug del rol) |
| `saved_reports` | SavedReportDoc (en `reporting/query/reports.py`, no en `models/`) | ✅ | — | ✅ | `id` (uuid) |
| `audit_log` | (sin modelo) | — | — | append-only | `ObjectId` auto |

> **`column_catalog` fue RETIRADA (doc 54, 2026-08-22).** Pertenecía al planteamiento inicial de un agente conversacional embebido, hoy descartado por completo: el adaptador ya no la pre-crea y el reset destructivo (`scripts/reset_for_migration.py`) la elimina junto con todo el schema. No confundir con `canonical_columns` (el store real de columnas de esta app).
>
> **Entidades virtuales del reporting** (no son colecciones): `COLL_OF` en `reporting/query/executor.py` mapea `view_columns → views` y `models → subject_areas`.
>
> **Tablas físicas en Lakebase:** el adaptador pre-crea 19 tablas (`KNOWN_COLLECTIONS` en `app/core/db/lakebase/collection.py` = las 19 de este mapa); `ddl_rules` y `ddl_ruleset_config` (doc 30) se crean on-demand con la misma forma `(id, doc jsonb)` + GIN.

---

## 2. Alcance del versionado

**`VERSIONED`** (`changesets/repository.py`, en orden de dependencia del apply): las 8 colecciones cuyos cambios pasan por el changeset (draft → submit → review → approve → publish):

```
projects · folders · subject_areas · schemas · canonical_tables · canonical_columns · relationships · views
```

Un cambio no toca la colección publicada hasta el **apply del approve**: vive como un doc en `changeset_changes` con el **doc completo** en `payload`. El `overlay(publicado, cambios)` produce el estado efectivo del draft.

**Un changeset pertenece a UN proyecto** (`changesets.projectId`, doc 75 D2): cada cambio del draft debe ser de una entidad de ese proyecto (guard I1: `payload.projectId` lo estampa el servidor con el del changeset; una referencia a una entidad de OTRO proyecto — tabla, columna, dominio, esquema — es 409 `CrossProjectError`, guard I2). Las versiones (`versionLabel` `vN`), la producción vigente y el rollback son **por proyecto**: `v3` de «Modelo DDV» y `v3` de «UDV INT FISICO» son versiones distintas e independientes.

**Ciclo de vida del proyecto (doc 75 D5)**: `projects` sigue en `VERSIONED`, pero con asimetría: **crear** es directo (`POST /api/projects` = doc + estándares vacíos o copiados + marcador `v1`, en una request); **renombrar/describir/borrar** van SIEMPRE por un draft del propio proyecto (guard `entityId == cs.projectId`). El borrado es UN cambio `projects/<pid> op=delete`; al aplicarse (approve con unanimidad) el servidor corre `cascade_delete`: soft-delete (`flgactive:false` + `deletedAt` + `deletedIn:<csId>`) de TODO lo del proyecto (`CASCADE_COLLECTIONS` = `PROJECT_SCOPED` menos `changesets` y `standards_versions`, que se conservan como historial) y `$pull` del pid en `users.projectIds`. Después, toda ruta `/api/projects/{pid}/…` responde 404 «Project not found.» y las operaciones sobre sus changesets 409 «This project was deleted.». Restaurar un proyecto borrado está fuera de alcance.

**Data Standards** se versiona **aparte** (no por el changeset) y **por proyecto** (doc 75 D3): `parent_domains`, `glossary_terms`, `udp_definitions`, `naming_config` y (doc 30) `ddl_rules` + `ddl_ruleset_config` del proyecto se escriben directo a producción y cada apply/rollback deja una versión con **snapshot completo** en `standards_versions` con el `projectId` del proyecto; `seq`/`label` (`vN`) arrancan en 1 en cada proyecto. Las reglas DDL y su config mutan SOLO vía `POST /api/projects/{pid}/standards/apply` (`rulesUpsert`/`rulesDelete`/`ddlConfigPatch`); el router `/api/projects/{pid}/ddl-rules` es de lectura. Un proyecto nuevo nace con estándares vacíos o copia bloques (`glossary` · `domains` · `udp` · `naming` · `ddl`) de otro proyecto (`copyFrom`, versión `kind=copy`).

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
| description | str? | null | definición funcional |
| udpValues | dict[str,str] | {} | `{udpDefId: value}` (valores de las etiquetas UDP nivel table) |

### `canonical_columns` — CanonicalColumnDoc
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK |
| projectId | str | — | ref `projects.id` (= el de su tabla) |
| tableId | str | — | ref `canonical_tables.id` |
| physicalName | str | — | |
| logicalName | str | — | |
| parentDomainId | str? | null | ref `parent_domains.id` (un dominio DEL MISMO proyecto) |
| dataType | str | — | tipo físico |
| typeOverridden | bool | false | true = `dataType` es override manual (no hereda del dominio) |
| isPrimaryKey | bool? | null | |
| pkPosition | int? | null | orden **0-based dentro de la PK** (≠ `ordinal`; ver doc 19 §12b) |
| isForeignKey | bool? | null | |
| isNullable | bool | true | |
| isPartition | bool | false | columna de partición (DDL emite `PARTITIONED BY`) |
| description | str? | null | definición funcional a nivel columna |
| ordinal | int | 0 | **orden único** de la columna en la tabla (el mismo en el modelo lógico y el físico; doc 74) |
| udpValues | dict[str,str] | {} | `{udpDefId: value}` |

> Dos órdenes distintos y **ambos** persistidos: `ordinal` (orden de columnas, único para ambas facetas) y `pkPosition` (orden de la llave). Erwin/DDL usan el de la llave para `PRIMARY KEY(...)`. La migración Erwin llena `ordinal` con «llaves primero + Column order» (doc 74).

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

**Embebido `RelationshipPairDoc`**: `{ parentColumnId: str (ref canonical_columns.id), childColumnId: str (ref canonical_columns.id), roleName: str? }`. (`roleName` = rolename estilo Erwin; el nombre real de la columna vive en el hijo.)

> Docs legacy v1 (`sourceTableId`/`targetTableId`, un solo par) se **normalizan a v2** al leer (`_upgrade_legacy` → `parent/child` + `pairs`).

### `views` — ViewDoc
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK |
| projectId | str | — | ref `projects.id` (alcance; las fuentes son tablas del mismo proyecto) |
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
| customSql | str? | null | doc 61: script del modo **Personalizada** — NO vacío ⇒ el cuerpo del `CREATE VIEW` ES este script (validado con sqlglot `databricks` al escribir; anti-`SELECT *`); null ⇒ **Regular** (DDL desde `sources`) |
| customColumns | list[dict] | [] | doc 61: columnas de salida `{name, expression?}` DERIVADAS del parse de `customSql` — el backend las RE-deriva en cada escritura (create/update directo y apply del changeset) |
| udpValues | dict | {} | doc 61: `{udpDefId: value}` — keys de `udp_definitions` con `level='view'` (mismo contrato que tablas/columnas/canvas) |

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
| appliedAt | str? | null | timestamp del apply EXITOSO a producción (`approved` sin `appliedAt` = murió a mitad; recuperable con `scripts/reapply_changeset.py`) |
| restoredFrom | dict? | null | doc 65: `{id, versionLabel}` de la versión restaurada por un rollback |

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
| before | dict? | null | imagen PREVIA de la entidad publicada (alimenta el rollback = draft inverso) |
| beforeAt | str? | null | `before=null` + `beforeAt` estampado = la entidad no existía (inverso = delete) |
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

### `glossary_terms` — AbbreviationDoc  *(diccionario de abreviaturas lógico↔físico)*
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| id | str | uuid4 | PK |
| projectId | str | — | ref `projects.id` (alcance; cambiar un término re-physicaliza SÓLO ese proyecto) |
| term | str | — | palabra lógica |
| abbrev | str | — | abreviatura física |
| scope | str | "column" | `column` \| `table` |
| wordType | str? | null | `prime` \| `class` \| `modifier` |
| locked | bool | false | lock por admin (intocable para todos hasta desbloquear) |
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
| defaultValue | str? | null | |
| allowedValues | list[str] | [] | enum cuando `dataType='list'` (p. ej. `[DAC, NO DAC, …]`) |
| description | str? | null | |

### `naming_config` — NamingConfigDoc
**1 doc por (proyecto, scope)**; `_id = "<projectId>:<scope>"` (`scope.naming_id`; no hay campo `id`).
| Campo | Tipo | Default | Notas |
|---|---|---|---|
| scope | str | — | `column` \| `table` (parte de la clave natural) |
| projectId | str | — | ref `projects.id` (parte de la clave natural) |
| separator | str | "" | separador al concatenar términos |
| case | str | "upper" | `upper` \| `lower` \| `camel` |
| maxLength | int | 150 | límite de caracteres del nombre físico |

Si falta un scope en un proyecto, el repo lo siembra con `DEFAULTS` (`separator:"", case:"upper", maxLength:150` para ambos); el kit lo siembra con `$setOnInsert` (no pisa un naming ya editado).

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
- **Colecciones con alcance** (`core/scope.py PROJECT_SCOPED`): `folders`, `subject_areas`, `schemas`, `canonical_tables`, `canonical_columns`, `relationships`, `views`, `parent_domains`, `glossary_terms`, `udp_definitions`, `naming_config`, `ddl_rules`, `ddl_ruleset_config`, `changesets`, `standards_versions`, `saved_reports`.
- **Reglas DDL** (`ddl_rules/models.py`): `RULE_KINDS = (rule, generator)`, `RULE_TARGETS = (column, table)`, `VALIDATION_STATES = (valid, invalid, stale)`.
- **Estado de changeset**: `draft`, `submitted`, `approved`, `rejected`.
- **Estado de usuario**: `active`, `invited`, `disabled`.
- **naming case**: `upper`, `lower`, `camel`. **naming scope**: `column`, `table`.

---

## 13. Hechos del estado actual relevantes para una migración de esquema

Solo **descripción del estado actual** (no recomendaciones de destino):

1. **Estructuras embebidas / denormalizadas** (viven dentro de un doc, sin sub-colección): `udpValues` (map en tables/columns/subject_areas/views), `layout` (map → `{x,y}`) y `drawings` (list) en `subject_areas`, `pairs` en `relationships`, `sources` en `views`, `approvals`/`comments` en `changesets`, `snapshot`/`diff`/`impact` en `standards_versions`, `permissions` en `roles`, `spec` (QuerySpec) en `saved_reports`, `udpRefs`/`action` en `ddl_rules`, `lookups`/`functions` en `ddl_ruleset_config`.
2. **Referencias sin integridad referencial**: todo apunta por string (id o **nombre** — `schema` y `sources[].column` son por nombre). Nada lo valida el almacén.
3. **Soft-delete** por `flgactive:false` + `deletedAt` en casi todo; `audit_log` es append-only; `changesets`/`changeset_changes`/`standards_versions` no usan `flgactive`.
4. **`_id` especiales**: `changeset_changes._id` es DETERMINISTA (`{csId}::{collection}::{entityId}`); `naming_config._id = "<projectId>:<scope>"`; `users._id = username`; `roles._id = key`; `ddl_ruleset_config._id = projectId`; `audit_log._id` = ObjectId auto. El resto = `id` uuid4 (los migrados desde Erwin: `uuid5("<projectId>|<Long_Id>")`, doc 75 D11 — el mismo objeto Erwin cargado en dos proyectos tiene ids distintos).
5. **Alias `schema`**: en `canonical_tables` y `views` el atributo Python es `sql_schema` pero el campo persistido es `schema`.
6. **Campos legacy/aditivos fuera del modelo**: por `extra="ignore"`, los docs persistidos pueden traer `migratedFrom`, `erwinLongId`, `flgactive`, `deletedAt`, `deletedIn` (cascada de borrado de proyecto), y (relaciones pre-v2) `sourceTableId`/`targetTableId`. **Están en la BD aunque el modelo no los liste** — mirar el doc real al migrar.
7. **Colecciones sin `*Doc`**: `saved_reports` (modelo en `reporting/query/reports.py`) y `audit_log` (shape en `core/audit.py`).
8. **Retiradas**: `column_catalog` (del planteamiento inicial con agente conversacional, descartado — doc 54): ya no se pre-crea ni se preserva; el reset destructivo la elimina.
9. **Foto de la data**: la BD vigente (cargada antes del doc 75, 2026-09-07) tiene los 4 XML de `folder_data/` en 2 proyectos y SIN `projectId` en los docs de estándares — es incompatible con este esquema y se reemplaza con el one-shot `scripts/run_migration.py --folder ../folder_data --apply --force` (destructivo), que deja **3 proyectos** según el manifiesto `folder_data/projects.json`: «Modelo DDV» (CPYBCA + Otros), «UDV INT FISICO» y «UDV INT LOGICO» (un archivo cada uno), cada uno con sus estándares (unión distinta de sus archivos), su ruleset DDL base y su `v1`.
10. **Versiones vigentes (por proyecto)**: cada proyecto tiene su `v1 Base` — un changeset MARCADOR (status `approved`, `appliedAt` estampado, 0 docs en `changeset_changes`, `projectId` del proyecto) creado por `scripts/mark_base_version.py` (o por `POST /api/projects` para un proyecto nuevo); sin él la web bloquea el módulo Model para ese proyecto. Las cargas de migración Erwin escriben DIRECTO a las colecciones publicadas, sin crear changesets. Data Standards de cada proyecto tiene `v1` (baseline) + `v2` "Base — DDL export rules"; los estándares vivos DERIVARON de esos snapshots — no hacer rollback de Standards de un proyecto hasta registrar una baseline nueva en ese proyecto.

---

## Ver también
- `arquitectura.md` §7 — visión de colecciones, erDiagram e índices.
- `consideraciones-y-limites.md` §4.6 — lista completa de índices (`ensure_indexes`).
- `migracion-erwin.md` — cómo se puebla este esquema desde un XML de Erwin.
