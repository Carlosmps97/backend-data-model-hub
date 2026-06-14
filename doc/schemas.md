# Schemas (Pydantic v2)

> El backend de plataforma tiene **dos familias** de schemas Pydantic:
>
> - `src/db/db_models.py` → **modelos de persistencia** (Cosmos DB),
>   espejo exacto de las interfaces TypeScript del frontend en
>   `web-data-model-hub/src/types/model.ts` y `types/auth.ts`.
> - `src/excel_import/schemas.py` → **contrato HTTP** del endpoint de
>   preview del Excel-import.
>
> Los request/response de los demás endpoints viven inline en cada
> router (`api/routes/*.py`) como clases Pydantic locales.

Todos los modelos de persistencia usan `ConfigDict(extra="ignore",
populate_by_name=True)`:

- `extra="ignore"` — los campos internos de Mongo (`flgactive`, `deletedAt`,
  `_id`, `projectId` en los hijos) y los campos legacy (p. ej. `scope`/`modelId`
  en permisos) se ignoran silenciosamente al validar.
- `populate_by_name=True` — permite construir con el nombre Python *o* el alias
  JSON (necesario para `sql_schema` ↔ `"schema"`).

---

## 1. Jerarquía embebida en el proyecto

### 1.1 `ModelLevelDoc` — un "Modelo (nivel)"

```python
class ModelLevelDoc(BaseModel):
    id: str
    name: str                 # código corto, p. ej. "RDV"
    full: str | None = None   # nombre completo, p. ej. "Raw Data Vault"
    color: str | None = None
    order: int = 0            # posición izquierda→derecha en el canvas + adyacencia
    engine: str | None = None
    desc: str | None = None
```

`order` define las bandas del canvas y qué relaciones cuentan como "entre capas".

### 1.2 `DomainDoc` — un Dominio (producto de datos, vertical)

```python
class DomainDoc(BaseModel):
    id: str
    name: str
    color: str | None = None
    owner: str | None = None
    steward: str | None = None
    sensitivity: str | None = None   # 'Public'|'Internal'|'Confidential'|'Restricted'|'PII'
    description: str | None = None
    subdomains: list[str] = []       # etiquetas libres seleccionables en una tabla
    layers: list[str] = []           # ids de ModelLevel que el dominio atraviesa
```

`sensitivity` se guarda como `str` (no `Literal`) por resiliencia ante valores
legacy / escritos por el agente.

### 1.3 `ProjectDoc` (colección `projects`)

```python
class ProjectDoc(BaseModel):
    id: str
    name: str
    description: str | None = None
    engines: list[str] = []          # engines del proyecto
    layers: list[ModelLevelDoc] = [] # niveles (RDV/UDV/DDV…)
    domains: list[DomainDoc] = []    # dominios verticales
    createdAt: str                   # ISO 8601
    updatedAt: str
```

`engines`, `layers` y `domains` son arreglos chicos **embebidos** (siempre se
leen con el proyecto y nunca se consultan por separado). Se editan vía
`PUT /api/projects/{id}`.

---

## 2. Canvas: tablas y relaciones

### 2.1 `TagDoc`

```python
class TagDoc(BaseModel):
    key: str = ""
    value: str = ""
```

Pares key-value libres. El validator `_coerce_tags` convierte la forma legacy
`list[str]` a `{key: "", value: <str>}` para que docs antiguos carguen sin
migración.

### 2.2 `TableColumnDoc`

```mermaid
classDiagram
    class TableColumnDoc {
        +str id  «uuid, default_factory»
        +str name
        +str? logicalName
        +str dataType
        +int? length
        +int? scale
        +bool? isPrimaryKey
        +bool? isSurrogateKey
        +bool? isPartitionKey
        +bool? isForeignKey
        +ForeignKeyRefDoc? foreignKeyRef
        +bool? isNullable
        +bool? isUnique
        +str? defaultValue
        +str? functionalDefinition
        +str? observations
    }
    class ForeignKeyRefDoc {
        +str table
        +str column
    }
    TableColumnDoc --> ForeignKeyRefDoc
```

> **Punto crítico**: `id` es un identificador **estable** que sobrevive a
> renames. Los endpoints de relación (`RelEndpointDoc.column`) apuntan a este
> `id`, **no a `name`**. `default_factory=lambda: str(uuid.uuid4())` garantiza id
> en código; `canvas_db._ensure_column_ids` hace además un backfill defensivo
> sobre cualquier dict que llegue sin id.

### 2.3 `ViewDoc` (embebida en la tabla)

```python
class ViewDoc(BaseModel):
    id: str
    name: str
    type: str = "business"   # 'business' | 'technical'
    role: str | None = None
    select: str = ""         # multilínea: una expresión por línea (CONCAT, sha256, CASE…)
    where: str = ""          # multilínea: condiciones unidas con AND
```

Las vistas son proyecciones SQL por rol. **No están en una colección aparte**:
se embeben en `TableDoc.views[]`. No forman parte del diagrama ER; se exportan
con el DDL.

### 2.4 `TableDoc` (colección `project_tables`)

```python
class TableDoc(BaseModel):
    id: str
    sql_schema: str | None = Field(default=None, alias="schema")  # la key Mongo es "schema"
    name: str
    logicalName: str | None = None
    layer: str | None = None        # id de ModelLevel (capa)
    domain: str | None = None       # id de Domain
    subdomain: str | None = None
    color: str | None = None        # heredado del dominio
    functionalDefinition: str | None = None
    applicationCode: str | None = None
    columns: list[TableColumnDoc] = []
    views: list[ViewDoc] = []
    tags: list[TagDoc] | None = None
    partition: PartitionSpecDoc | None = None
    position: NodePositionDoc | None = None
    # NOTA: `projectId` NO está en el schema — se inyecta/elimina al persistir.
```

`PartitionSpecDoc` lleva `strategy`, `columns`, `buckets`, `granularity`,
`clusteringColumns`. `NodePositionDoc` lleva solo `x: float, y: float` y se
escribe por el endpoint dedicado `PATCH /api/projects/{id}/positions`.

### 2.5 `RelationshipDoc` (colección `project_relationships`)

```python
class RelEndpointDoc(BaseModel):
    table: str     # TableDoc.id
    column: str    # TableColumnDoc.id

class RelationshipDoc(BaseModel):
    id: str
    source: RelEndpointDoc
    target: RelEndpointDoc
    cardinality: str = "1:N"   # '1:1' | '1:N' | 'N:N'
```

Forma **anidada** `source`/`target`. Puede cruzar capas y dominios; las banderas
`crossLayer`/`skip` se **derivan en el frontend** del orden de los niveles y
**no se persisten**.

```mermaid
flowchart LR
    P[("projects<br/>(engines + layers + domains)")]
    PT[("project_tables<br/>(shard projectId · embebe views[])")]
    PR[("project_relationships<br/>(shard projectId · source/target/cardinality)")]
    P -->|"projectId"| PT
    P -->|"projectId"| PR
    P --> CV["Canvas (en memoria)<br/>{ tables, relationships }"]
    PT --> CV
    PR --> CV
```

---

## 3. Usuarios y permisos

```python
class PermissionDoc(BaseModel):
    projectId: str
    level: Literal["view", "edit"]

class UserDoc(BaseModel):
    id: str
    username: str
    passwordHash: str
    role: Literal["admin", "editor", "viewer"]
    isActive: bool
    permissions: list[PermissionDoc] = []
    createdAt: str
    statusUpdatedAt: str
    lastLoginAt: str | None = None
```

| Campo | Notas |
|---|---|
| `passwordHash` | bcrypt con `gensalt(rounds=10)`. **Nunca sale del backend** — `_public_user` (admin) y `_user_payload` (auth) lo omiten. |
| `role` | rol "global"; los permisos finos viven en `permissions`. |
| `isActive` | desactivar bloquea inmediatamente — `require_user` revalida en cada request. |
| `permissions` | grants **por proyecto** (`{projectId, level}`). El scope-modelo legacy (`scope`/`modelId`) se ignora con `extra="ignore"`. |

`role=admin` cortocircuita los permisos (`is_admin` → `edit` en todo).

---

## 4. Schemas del Excel-import (`src/excel_import/schemas.py`)

Centralizados porque definen el contrato HTTP del preview. Usan
`ConfigDict(extra="forbid", populate_by_name=True)` — más estricto que los
modelos de DB.

### 4.1 `PreviewColumn`

```python
class PreviewColumn(BaseModel):
    name: str
    dataType: str          # canónico (p. ej. "decimal", "varchar")
    rawDataType: str       # original tal como vino en el Excel
    length: int | None = None
    scale: int | None = None
    functionalDefinition: str | None = None
    typeConfidence: float            # 0-100
    typeMatchedVia: MatchKind        # "exact"|"alias"|"fuzzy"|"unknown"|"empty"
```

| `typeMatchedVia` | Cuándo | UI hint |
|---|---|---|
| `exact` | coincide literal con un canónico | sin badge |
| `alias` | sinónimo conocido (`int → integer`) | badge gris |
| `fuzzy` | rapidfuzz aceptó por similitud (`decximam → decimal`) | badge azul + `%` |
| `unknown` | nada superó el `FUZZY_THRESHOLD` | badge naranja (corregir) |
| `empty` | celda vacía; default a `varchar` | badge naranja |

### 4.2 `PreviewTable` y `ExcelPreview`

```python
class PreviewTable(BaseModel):
    sheetName: str
    schema_: str | None = Field(default=None, alias="schema")  # JSON: "schema"
    name: str
    description: str | None = None
    columns: list[PreviewColumn]

class ExcelPreview(BaseModel):
    tables: list[PreviewTable]
    tableDescriptionsFound: bool
    warnings: list[str]
```

`description` se popula desde la hoja opcional `TablesDescriptions`. `warnings`
lista avisos no fatales (hojas vacías, descripciones huérfanas).

---

## 5. Request/response schemas inline (por router)

### `api/routes/auth.py`

```python
class LoginRequest(BaseModel):
    username: str
    password: str
```

Response del login: `_user_payload(user)` (dict sin `passwordHash`) envuelto en
`ok(...)`.

### `api/routes/projects.py`

```python
class CreateProjectRequest(BaseModel):
    name: str
    description: str | None = None
    engines: list[str] | None = None
    layers: list[dict[str, Any]] | None = None    # ModelLevel[]
    domains: list[dict[str, Any]] | None = None   # Domain[]

class UpdateProjectRequest(BaseModel):  # todos opcionales
    name | description | engines | layers | domains
```

`layers`/`domains` se tipan como `list[dict]` y se validan con
`ProjectDoc.model_validate` en el handler (deja que el frontend mande el JSON
completo de la jerarquía sin disparar 422 prematuros).

### `api/routes/canvas.py`

```python
class CanvasReplaceRequest(BaseModel):
    tables: list[dict[str, Any]] | None = None
    relationships: list[dict[str, Any]] | None = None

class PositionPayload(BaseModel):
    x: float
    y: float

class PatchPositionsRequest(BaseModel):
    tables: dict[str, PositionPayload] | None = None   # tableId → {x, y}
```

`PUT /canvas` exige al menos uno de `tables` / `relationships` (400 si ambos son
`None`). Las listas son `list[dict]`: la validación estricta la hacen
`TableDoc` / `RelationshipDoc` dentro de `replace_canvas`.

### `api/routes/admin.py`

```python
class PermissionPayload(BaseModel):
    projectId: str
    level: str                  # "view" | "edit"

class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str                   # "admin" | "editor" | "viewer"
    isActive: bool = True
    permissions: list[PermissionPayload] = []

class UpdateUserRequest(BaseModel):  # todos opcionales
    ...
```

`_validate_role` y `_validate_permissions` levantan **400** con mensajes
específicos (mejor que un 422 genérico de Pydantic). Para `role=admin`,
`permissions` se ignora (acceso total).

### `api/routes/health.py`

```python
class HealthResponse(BaseModel):
    status: str
    version: str
    db_connected: bool
```

---

## 6. Envelope de respuesta

Todos los endpoints CRUD usan `ok(...)` de `src/api/response_builder.py`:

```python
def ok(data: Any = None) -> dict[str, Any]:
    return {"success": True, "data": data}
```

El frontend espera siempre `{success: bool, data?, error?}`. Los errores se
levantan con `HTTPException(status, detail)` (FastAPI → `{"detail": "..."}`),
salvo `login` / `me`, que devuelven explícitamente
`{"success": False, "error": "...", "code"?: "..."}` para diferenciar
`unauthenticated` de `inactive`.
