# backend-data-model-hub

Backend de **plataforma** del Data Modeler. Maneja autenticación,
permisos, proyectos, modelos y persistencia en Cosmos DB. NO contiene
al agente de modelado conversacional — ese vive en
[`app-agents-modeler`](../app-agents-modeler/README.md).

> **Stack**: Python 3.12 · FastAPI · Pydantic v2 · Motor (async MongoDB) ·
> bcrypt · PyJWT · Azure Cosmos DB for MongoDB (vCore).

## Topología del sistema

```
┌─────────────────────────────────────────────────────────┐
│                Data Modeler (Next.js :3000)             │
│  ┌──────────────┐   ┌──────────────┐   ┌─────────────┐  │
│  │  Editor ER   │   │  AI Chat     │   │  Auth UI    │  │
│  │  (canvas)    │   │  Panel       │   │             │  │
│  └──────┬───────┘   └──────┬───────┘   └──────┬──────┘  │
└─────────┼──────────────────┼──────────────────┼─────────┘
          │ cookie           │ CORS            │ cookie
          │ modeler-auth     │ allowlist        │ modeler-auth
          ▼                  ▼                  ▼
   ┌──────────────┐  ┌──────────────┐  ┌──────────────┐
   │  backend     │  │  app-agents  │  │  backend     │
   │  :8000       │  │  -modeler    │  │  :8000       │
   │  (este repo) │  │  :8001       │  │              │
   └──────┬───────┘  └──────┬───────┘  └──────┬───────┘
          │                 │                 │
          └─────────────────┼─────────────────┘
                            ▼
                  ┌─────────────────┐
                  │  Cosmos DB      │
                  │  (db_modeler)   │
                  └─────────────────┘
```

| Servicio | Puerto | Cookies | Colecciones Cosmos |
|---|---|---|---|
| `backend-data-model-hub` (este) | `:8000` | sí (`modeler-auth`) | `users`, `projects`, `models`, `model_tables`, `model_relationships`, `model_views` |
| `app-agents-modeler`            | `:8001` | no (CORS allowlist)   | `column_catalog` |

Ambos apuntan al **mismo cluster Cosmos** y a la **misma database**, pero
tocan colecciones disjuntas.

## Endpoints

| Método | Ruta | Descripción |
|---|---|---|
| `GET`    | `/api/health`                                | Estado de la API + flag `db_connected`. |
| `POST`   | `/api/auth/login`                            | Login con username/password (form). |
| `POST`   | `/api/auth/logout`                           | Borra cookie `modeler-auth`. |
| `GET`    | `/api/auth/me`                               | Usuario autenticado (200) o 401. |
| `GET`    | `/api/projects`                              | Lista proyectos visibles para el user. |
| `POST`   | `/api/projects`                              | Crea proyecto (admin). |
| `GET`    | `/api/projects/{id}`                         | Detalle (con permisos). |
| `PUT`    | `/api/projects/{id}`                         | Update (admin o edit). |
| `DELETE` | `/api/projects/{id}`                         | Borra (admin). |
| `GET`    | `/api/models`, `/api/models/{id}`            | Idem para modelos. |
| `POST`/`PUT`/`DELETE` `/api/models[/{id}]`   | CRUD de modelos. |
| `GET`/`POST`/`PUT`/`DELETE` `/api/admin/users[/{id}]` | Gestión de usuarios y permisos (admin). |

Los endpoints de modelado conversacional (`/api/conversations/*`,
`/api/engines`) **viven en `app-agents-modeler`**, no acá.

## Quickstart (local, macOS)

```bash
# 1. Configurar env
cp .env.example .env
# (rellenar COSMOS_CONNECTION_STRING + AUTH_SECRET)

# 2. Crear venv con Python 3.12
/opt/homebrew/bin/python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Levantar el servidor en :8000
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# 4. Probar
curl http://localhost:8000/api/health
# → {"status":"ok","version":"1.0.0","db_connected":true}
```

## Variables de entorno

Ver `.env.example`. Las críticas:

- `COSMOS_CONNECTION_STRING` + `COSMOS_DATABASE`: cuenta Cosmos DB.
  Debe coincidir con la del agente.
- `AUTH_SECRET`: HMAC-SHA256 de 64 hex chars. **Debe ser idéntico al
  `AUTH_SECRET` del frontend Next.js** (`web-data-model-hub/.env`).
- `CORS_ORIGINS` (opcional): lista separada por coma de orígenes del
  frontend permitidos. Default: `http://localhost:3000`.
- `LOG_FORMAT`: `pretty` (dev) o `json` (producción).
- `LOG_LEVEL`: `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL`.

## Estructura

Monolito modular: `app/core/` (infra compartida) + `app/features/<x>/` (vertical
slices). Cada feature se importa SOLO por su `__init__` (API pública). Detalle y
"cómo agregar una feature" en [`doc/feature-architecture.md`](doc/feature-architecture.md).

```
app/
  main.py                  create_app(): lifespan (Motor) + CORS + logging + monta routers
  core/                    infraestructura compartida (no depende de features)
    config.py · logging.py · models.py (DOC_CONFIG · TagDoc · coerce_tags)
    db/        client.py (connect/disconnect/get_db) · indexes.py (ensure_indexes)
    security/  jwt.py (HS256 + cookie) · passwords.py (bcrypt)
    api/       envelope.py (ok({...}))
  features/<x>/            router.py · service.py · repository.py · schemas.py · models.py · __init__.py
    health/                GET /api/health
    auth/                  login/logout/me · dependencies (auth/permisos) · bootstrap admin
    users/                 CRUD usuarios + permisos (admin)
    projects/              CRUD proyectos (engines/layers/domains embebidos)
    canvas/                tablas + relaciones (get/replace/positions) — invariante extra="ignore"
    metadata/              catálogo Semantic Types / UDP (transversal)
    excel_import/          preview de .xlsx (reader · normalizer · service)
api/
  main.py                  shim de compatibilidad → `from app.main import app`
scripts/                   migraciones + seeds (seed_lakehouse · seed_udp_catalog · migrate_*)
```

## Despliegue

`Dockerfile` multi-stage (Python 3.12-slim). Puerto expuesto: `8000`.
Para Azure App Service, definir `WEBSITES_PORT=8000` y las env vars
de arriba.

## Servicio de agentes

Este backend **no** ejecuta agentes. Para generar modelos de datos a
partir de Excel + lineamientos, el frontend invoca directamente al
servicio `app-agents-modeler` en `:8001`. Ver
[`app-agents-modeler/README.md`](../app-agents-modeler/README.md).
