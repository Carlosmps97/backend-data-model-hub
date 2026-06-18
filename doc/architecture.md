# Arquitectura — `backend-data-model-hub`

> Backend **de plataforma** del Data Modeler. Resuelve autenticación,
> permisos, CRUD de **proyectos** (con su jerarquía embebida), persistencia
> del **canvas** (tablas + relaciones), administración de usuarios y la
> previsualización del import desde Excel. **No** ejecuta agentes LLM: el
> modelado conversacional vive en el servicio separado `app-agents-modeler`.

## 1. Visión general

```mermaid
flowchart LR
    subgraph Browser["Browser · Next.js (:3000)"]
        UI["React UI<br/>Zustand stores"]
        PXY["proxy.ts<br/>(verifica JWT por request)"]
        SVC["src/services/*"]
    end

    subgraph Platform["backend-data-model-hub (:8000)"]
        H["/api/health"]
        AUTH["/api/auth/*"]
        PRJ["/api/projects/*"]
        CNV["/api/projects/{id}/canvas<br/>/api/projects/{id}/positions"]
        ADM["/api/admin/users/*"]
        XLS["/api/excel-import/preview"]
    end

    subgraph Agent["app-agents-modeler (servicio aparte)"]
        AGT["modelado conversacional<br/>(desacoplado)"]
    end

    subgraph Cloud["Azure"]
        COSMOS[("Cosmos DB<br/>db_modeler")]
    end

    UI --> SVC
    SVC -.cookie modeler-auth.-> AUTH
    SVC --> PRJ
    SVC --> CNV
    SVC --> ADM
    SVC --> XLS
    PXY -. verifica firma .- AUTH
    AUTH --> COSMOS
    PRJ --> COSMOS
    CNV --> COSMOS
    ADM --> COSMOS
    AGT -. column_catalog .-> COSMOS
```

**Stack**: Python 3.12 · FastAPI · Pydantic v2 · Motor (async MongoDB) ·
bcrypt · PyJWT · openpyxl · rapidfuzz · Azure Cosmos DB for MongoDB.

**Procesos en juego**: 1 proceso FastAPI por deploy + 1 cuenta de Cosmos DB.
No hay broker de colas, ni caché Redis, ni dependencia de Azure AI Foundry
(eso vive solo en `app-agents-modeler`). El backend arranca en segundos y
tiene huella de RAM baja.

### Topología de colecciones (`db_modeler`)

| Servicio | Colecciones que toca |
|---|---|
| `backend-data-model-hub` (este repo) | `users`, `projects`, `project_tables`, `project_relationships` |
| `app-agents-modeler` | `column_catalog` (diccionario semántico del agente) |

Ambos apuntan al **mismo cluster y database** pero tocan colecciones
disjuntas — no hay carreras de escritura entre ellos. Este backend **no toca**
`column_catalog`.

---

## 2. Modelo project-centric ("Aurora")

La jerarquía es **Proyecto → Modelo (nivel) → Dominio → Tabla → Columnas + Vistas**:

- Un **Proyecto** posee sus `engines[]`, sus `layers` (los "Modelos / niveles",
  p. ej. RDV·UDV·DDV) y sus `domains` (productos de datos **verticales** que
  cruzan niveles). Esos tres arreglos son chicos y se **embeben** en el
  documento del proyecto.
- Las **Tablas** viven en `project_tables` (shard key `projectId`), etiquetadas
  con `layer` (id de ModelLevel), `domain` (id de Domain) y `subdomain`, y
  **embeben** sus vistas SQL por rol (`views[]`).
- Las **Relaciones** viven en `project_relationships` (shard key `projectId`)
  con la forma anidada `source`/`target`/`cardinality`.

No existe la noción de "un diagrama por modelo": hay **un único canvas por
proyecto**, y lo que se ve se controla en el frontend con el *Scope*.

---

## 3. Capas del sistema

> **Monolito modular.** `app/core/` (infraestructura compartida) +
> `app/features/<x>/` (vertical slices: `router · service · repository · schemas
> · models`). Cada feature se importa solo por su `__init__` (API pública).
> Convención y "cómo agregar una feature" en
> [`feature-architecture.md`](feature-architecture.md).

### 3.1 Punto de entrada — `app/main.py`

`create_app()` arma la app FastAPI, configura CORS + middleware de logging y
monta los routers de las features. `api/main.py` queda como **shim** de
compatibilidad (`uvicorn api.main:app` sigue vivo; el entrypoint canónico es
`app.main:app`). El **lifespan** abre Motor al arrancar y crea índices:

| Singleton | Tipo | Resiliencia ante fallo |
|---|---|---|
| `motor_client` (Cosmos DB) | `AsyncIOMotorClient` + `ensure_indexes` | si falla, `app.state.db_connected = False` y `/api/health` lo reporta; los endpoints que usen DB devuelven 500 |

### 3.2 Routers (`app/features/*/router.py`)

| Router | Prefijo | Auth | Responsabilidad |
|---|---|---|---|
| `health` | `/api/health` | público | liveness + flag `db_connected` |
| `auth` | `/api/auth/*` | público (`/login`, `/logout`); `/me` con cookie | login / logout / me con cookie `modeler-auth` |
| `projects` | `/api/projects/*` | `AuthUserDep` (admin para POST/DELETE) | CRUD de proyectos + su jerarquía embebida (engines/layers/domains) |
| `canvas` | `/api/projects/{id}/canvas`, `/api/projects/{id}/positions` | `AuthUserDep` + `check_project_access` | hidratar y reemplazar tablas+relaciones; persistir posiciones |
| `users` | `/api/admin/users/*` | `AdminDep` | gestión de usuarios y permisos (solo admin) |
| `excel_import` | `/api/excel-import/preview` | `AuthUserDep` | preview normalizado de un `.xlsx` |

> No hay router `models`: el canvas reemplazó a la antigua colección `models`
> y sus colecciones hijas.

### 3.3 Infraestructura compartida (`app/core/`) + auth

```
app/core/
├── security/jwt.py        # JWT sign/verify (HS256) + cookie modeler-auth
├── security/passwords.py  # bcrypt hash/verify
├── api/envelope.py        # ok({...}) → {success: true, data: ...}
├── db/{client,indexes}.py # Motor singleton + ensure_indexes
└── config.py · logging.py · models.py (DOC_CONFIG · TagDoc compartido)
```

El bootstrap del admin vive en `app/features/auth/bootstrap.py`. El wiring de
autorización vive en `app/features/auth/dependencies.py` (API pública de la
feature `auth`): lee la cookie `modeler-auth`,
valida el JWT, **recarga `UserDoc` desde Cosmos en cada request** (un cambio de
permisos toma efecto al instante) y expone:

| Helper | Qué hace |
|---|---|
| `project_access_level(user, projectId)` | `"edit"` / `"view"` / `None` (admin → siempre `"edit"`) |
| `can_view_project` / `can_edit_project` | booleanos derivados del anterior |
| `visible_project_ids(user)` | conjunto de proyectos que el usuario puede ver (admin → todos) |
| `check_project_access(user, projectId, level)` | lanza 403/404 si no cumple |
| `require_user` / `require_admin` | dependencies `AuthUserDep` / `AdminDep` |

Replican el contrato de `web-data-model-hub/src/lib/auth/permissions.ts`.

### 3.4 Capa de datos — repositorios por feature

Cada feature tiene su `repository.py` (CRUD en Cosmos) y su `models.py` (Pydantic
v2, espejo de las interfaces TS del frontend, `extra="ignore"`):

```
app/features/projects/{models,repository}.py   # proyectos (soft-delete + cascade)
app/features/canvas/{models,repository}.py      # tablas + relaciones (get/replace/positions/delete)
app/features/users/{models,repository}.py       # usuarios (hard-delete; isActive=false = lock-out)
app/features/metadata/{models,repository}.py    # Semantic Types / UDP (catálogo global)
app/core/db/client.py                           # singleton AsyncIOMotorClient + get_db
```

**Colecciones e índices** (creados idempotentemente al arranque):

| Colección | Shard / PK | Índices |
|---|---|---|
| `users` | `_id` (uuid) | único en `username`; hard-delete |
| `projects` | `_id` (uuid) | soft-delete `flgactive`; embebe `engines`/`layers`/`domains`; cascade al canvas |
| `project_tables` | `projectId` | `(projectId)`, `(projectId, layer)`, `(projectId, domain)`; embebe `views[]` |
| `project_relationships` | `projectId` | `(projectId)`; forma anidada `source`/`target`/`cardinality` |

`canvas_db._replace_entities` hace **soft-delete + bulkWrite `ReplaceOne(upsert)`**:
marca los docs viejos del proyecto como `flgactive=false` y luego upserta los
nuevos, evitando `E11000` sobre filas previamente soft-eliminadas. `projectId`
se **inyecta al escribir y se elimina al leer** (no es campo de `TableDoc` /
`RelationshipDoc`). `_ensure_column_ids` stampa un UUID en cualquier columna que
llegue sin `id` (safety-net, ver §6).

### 3.5 Feature Excel-import — `src/excel_import/`

Módulo aislado (sin estado compartido) montado en `/api/excel-import/preview`.
Pipeline 100 % determinista (sin LLM): bytes → `workbook_reader` → `service` →
`type_normalizer` → `ExcelPreview`. Ver `doc/workflow.md`.

---

## 4. Autenticación

```mermaid
sequenceDiagram
    participant U as Browser
    participant PX as Next.js proxy.ts
    participant FA as backend-data-model-hub
    participant DB as Cosmos DB

    U->>FA: POST /api/auth/login {username, password}
    FA->>FA: ensure_bootstrap_admin (idempotent)
    FA->>DB: get_user_by_username
    DB-->>FA: UserDoc
    FA->>FA: bcrypt.checkpw + sign_token (HS256, 10h)
    FA->>DB: mark_login_success (best-effort)
    FA-->>U: 200 + Set-Cookie modeler-auth (HttpOnly, SameSite=Lax)

    U->>PX: GET /projects/abc (navegación HTML)
    PX->>PX: jose.jwtVerify(cookie)
    alt token válido
        PX-->>U: render page
    else token inválido o ausente
        PX-->>U: redirect /login?next=...
    end

    U->>FA: GET /api/projects (XHR con cookie)
    FA->>FA: verify_token
    FA->>DB: get_user_by_id (recarga viva)
    DB-->>FA: UserDoc actualizado
    FA->>FA: filtrar por permissions (visible_project_ids)
    FA-->>U: lista filtrada
```

**Detalles importantes**:

- **`AUTH_SECRET` compartido** entre `web-data-model-hub/.env` y este `.env`. El
  proxy de Next.js usa `jose` (Edge) y este backend usa `PyJWT`, ambos HS256 y
  10 h de vida. Si divergen → 401 silencioso en todas las requests (causa #1 de
  "el login se queda colgado").
- **Bootstrap admin**: el primer `POST /api/auth/login` crea `admin / dogadmin2019`
  si no existe ningún usuario `admin`. Idempotente (`src/api/auth.py::ensure_bootstrap_admin`).
- **Recarga viva por request**: `get_current_user` relee `UserDoc` desde DB; no
  confía en los claims del JWT, así que desactivar un usuario o cambiar permisos
  toma efecto sin esperar a que expire la cookie.
- **Permisos** (`src/api/dependencies.py`): granularidad **scope-proyecto**,
  niveles `view` / `edit`. Admin tiene `edit` total. (El antiguo scope-modelo
  desapareció: hay un canvas por proyecto, el proyecto es la unidad de acceso.)

---

## 5. Flujo end-to-end: abrir y editar un proyecto

```mermaid
sequenceDiagram
    participant FE as Frontend Next.js
    participant API as backend-data-model-hub
    participant DB as Cosmos DB

    Note over FE,DB: 1. Abrir el proyecto (hub + editor)
    FE->>API: GET /api/projects/{P}
    API->>DB: find_one projects (incluye engines/layers/domains)
    API-->>FE: ProjectDoc
    FE->>API: GET /api/projects/{P}/canvas
    API->>DB: find project_tables + project_relationships (paralelo, por projectId)
    API-->>FE: { tables, relationships } hidratado

    Note over FE,DB: 2. Editar modelos/dominios del proyecto
    FE->>API: PUT /api/projects/{P} (engines/layers/domains)
    API->>DB: update projects
    API-->>FE: ProjectDoc actualizado

    Note over FE,DB: 3. Editar tablas/relaciones (autosave del editor)
    FE->>API: PUT /api/projects/{P}/canvas (tables, relationships sin position)
    API->>API: _ensure_column_ids (stamp uuid si faltan)
    API->>DB: _replace_entities (soft-delete + bulkWrite upsert por projectId)
    API-->>FE: { tables, relationships }

    Note over FE,DB: 4. (opcional) Persistir posiciones del canvas
    FE->>API: PATCH /api/projects/{P}/positions {tableId: {x,y}}
    API->>DB: bulk_update_positions (solo el campo position)
    API-->>FE: {updated: N}
```

**Decisiones de diseño visibles en este flujo**:

1. El canvas se hidrata con **dos colecciones por `projectId`** (con índice), no
   con `$lookup` — Cosmos RU no recomienda joins amplios.
2. `PUT /canvas` **reemplaza en bloque** tablas y/o relaciones (lo que llegue en
   el body). El frontend lo invoca con debounce desde el editor.
3. `PUT /canvas` **omite `position`** a propósito (el store lo stripea): las
   posiciones se escriben solo por `PATCH /positions`, para que un Save en una
   pestaña no pise un drag de otra. En la práctica el editor actual trata las
   posiciones como **per-sesión** (auto-organiza al abrir).

---

## 6. Decisiones de diseño

### Por qué separar el backend de plataforma del backend del agente
El modelado conversacional (LLM) tiene un perfil de recursos muy distinto:
Azure AI Foundry, tokens-por-minuto, semáforos de concurrencia, dependencias
pesadas. Mantenerlo en `app-agents-modeler` permite escalar y desplegar ambos
de forma independiente. Este backend solo depende de Cosmos DB y del
`AUTH_SECRET` compartido con el frontend.

### Por qué project-centric (y no "models")
El rediseño Aurora unificó el modelo: un proyecto tiene un único canvas, con sus
niveles y dominios embebidos. Re-shardear las tablas/relaciones por `projectId`
da hidratación con latencia constante y elimina la capa intermedia "modelo =
diagrama" que antes complicaba permisos y navegación.

### Por qué `_ensure_column_ids` es defensivo
Los `id` estables de columna sobreviven a renames y son la base de las
relaciones FK (los endpoints apuntan a `TableColumn.id`, no a `name`). Un import
de Excel o un cliente desactualizado podría enviar columnas sin id; el helper es
idempotente, barato y mantiene la invariante de que **toda columna persistida
tiene un uuid**.

### Por qué el Excel-import no toca el LLM
La normalización de tipos es resoluble con regex + fuzzy (`rapidfuzz`, threshold
75). Es rápido, determinista y reentrante. Lo que no encaja queda como
`unknown` y el usuario corrige en la UI. Ver `doc/workflow.md`.
