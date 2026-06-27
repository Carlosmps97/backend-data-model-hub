# backend-data-model-hub

Backend de **plataforma** del Data Modeler. Maneja proyectos, modelos (canvas),
catálogo de metadata e import de Excel, con persistencia en Cosmos DB. **MVP sin
auth ni permisos** (abierto/anónimo — el modelo de permisos se reintroduce al
final como matriz robusta). NO contiene al agente de modelado conversacional —
ese vive en [`app-agents-modeler`](../app-agents-modeler/README.md), fuera del MVP.

> **Stack**: Python 3.12 (Databricks Apps usa 3.11) · FastAPI · Pydantic v2 ·
> Motor (async MongoDB) · openpyxl · rapidfuzz · Azure Cosmos DB for MongoDB (vCore).

## Topología del sistema

```
        Data Modeler (Next.js)                 app-agents-modeler
        ┌──────────────┐                       ┌──────────────┐
        │  Editor ER   │   fetch /api/*        │  agente IA   │
        │  (canvas)    │ ───────────────►      │  (separado)  │
        └──────┬───────┘                       └──────┬───────┘
               │                                      │ column_catalog
               ▼                                      ▼
        ┌──────────────┐                       ┌─────────────────┐
        │  backend     │ ────────────────────► │  Cosmos DB      │
        │  (este repo) │                       │  (db_modeler)   │
        └──────────────┘                       └─────────────────┘
```

| Servicio | Colecciones Cosmos |
|---|---|
| `backend-data-model-hub` (este) | `projects`, `project_tables`, `project_relationships`, `semantic_types`, `udps` |
| `app-agents-modeler`            | `column_catalog` |

Mismo cluster Cosmos y misma database, colecciones disjuntas.

## Endpoints (todos abiertos — MVP sin auth)

| Método | Ruta | Descripción |
|---|---|---|
| `GET` | `/api/health` | Estado de la API + flag `db_connected`. |
| `GET`/`POST` | `/api/projects` | Lista todos / crea proyecto. |
| `GET`/`PUT`/`DELETE` | `/api/projects/{id}` | Detalle / update / borra (+ cascade). |
| `GET`/`PUT` | `/api/projects/{id}/canvas` | Hidrata / reemplaza tablas + relaciones. |
| `PATCH` | `/api/projects/{id}/positions` | Persiste posiciones del canvas. |
| `GET`/`POST`/`PUT`/`DELETE` | `/api/metadata/semantic-types[/{id}]` | Catálogo de Semantic Types. |
| `GET`/`POST`/`PUT`/`DELETE` | `/api/metadata/udps[/{id}]` | Catálogo de UDPs. |
| `POST` | `/api/excel-import/preview` | Preview normalizado de un `.xlsx`. |

Los endpoints de modelado conversacional viven en `app-agents-modeler`, no acá.

## Quickstart (local, macOS)

```bash
cp .env.example .env            # rellenar COSMOS_CONNECTION_STRING
/opt/homebrew/bin/python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
curl http://localhost:8000/api/health
# → {"status":"ok","version":"1.0.0","db_connected":true}
```

## Variables de entorno

Ver `.env.example`:
- `COSMOS_CONNECTION_STRING` + `COSMOS_DATABASE` (default `db_modeler`).
- `CORS_ORIGINS` (opcional): orígenes del frontend permitidos. Default `http://localhost:3000`.
- `LOG_FORMAT`: `pretty` (dev) / `json` (prod) · `LOG_LEVEL`.

## Estructura

Monolito modular: `app/core/` (infra compartida) + `app/features/<x>/` (vertical
slices). Cada feature se importa SOLO por su `__init__`. Detalle y "cómo agregar
una feature" en [`doc/feature-architecture.md`](doc/feature-architecture.md).

```
app/
  main.py                  create_app(): lifespan (Motor) + CORS + logging + monta routers
  core/                    infra compartida (no depende de features)
    config.py · logging.py · models.py (DOC_CONFIG · TagDoc · coerce_tags)
    db/   client.py (connect/disconnect/get_db) · indexes.py (ensure_indexes)
    api/  envelope.py (ok({...}))
  features/<x>/            router · service · repository · schemas · models · __init__
    health/                GET /api/health
    projects/              CRUD proyectos (engines/layers/domains embebidos)
    canvas/                tablas + relaciones (get/replace/positions) — invariante extra="ignore"
    metadata/              catálogo Semantic Types / UDP (transversal)
    excel_import/          preview de .xlsx (reader · normalizer · service)
api/
  main.py                  shim de compatibilidad → `from app.main import app`
scripts/                   migraciones + seeds (seed_lakehouse · seed_udp_catalog · migrate_*)
```

## Despliegue

A **Databricks Apps** (no App Service). Bundle `databricks.yml` + `app.yaml` +
GitHub Actions. Guía completa en [`../DEPLOY-databricks.md`](../DEPLOY-databricks.md).
Entry: `app.main:app`.

## Servicio de agentes

Este backend **no** ejecuta agentes (viven en `app-agents-modeler`). La
integración del agente con el frontend está **fuera del MVP** actual.
