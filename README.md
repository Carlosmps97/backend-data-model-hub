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
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

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

```
api/
  main.py                       FastAPI entrypoint (port 8000)
  routes/
    health.py                   GET /api/health
    auth.py                     POST /api/auth/login,logout · GET /api/auth/me
    projects.py                 CRUD de proyectos (con permisos)
    models.py                   CRUD de modelos (con permisos)
    admin.py                    CRUD de usuarios + permisos (solo admin)
src/
  api/
    auth.py                     JWT (HS256) + bcrypt + bootstrap admin
    dependencies.py             Providers FastAPI: get_current_user, require_*,
                                project_access_level, model_access_level
    response_builder.py         ok({...}) helper
  config.py                     Settings (Cosmos + project root)
  db/
    motor_client.py             Motor (async MongoDB) connection singleton
    db_models.py                UserDoc, ProjectDoc, DataModelDoc,
                                TableModelDoc, RelationshipDoc, ViewModelDoc
    users_db.py                 CRUD async de `users`
    projects_db.py              CRUD async de `projects`
    models_db.py                CRUD async de modelos + child collections
  logger.py                     Logging estructurado (pretty/json)
scripts/
  backfill_column_ids.py        Migración: stamp UUID a columnas legacy
  prune_dangling_relationships.py  Limpia FK rotas
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
