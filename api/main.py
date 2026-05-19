"""API REST async del backend de plataforma (backend-data-model-hub).

Punto de entrada de FastAPI. Responsabilidades acotadas:
- Configurar logging.
- Lifespan: abrir/cerrar la conexión a Cosmos DB (Motor async).
- Middleware: logueo estructurado de cada request.
- CORS para el frontend Next.js (con credenciales — comparte cookie
  `modeler-auth` con el frontend).
- Montar los routers `health`, `auth`, `admin`, `projects`, `models`.

El servicio de agentes (modelado conversacional, ConversationStore,
FoundryChatClient) vive en `app-agents-modeler` y se invoca desde el
frontend directamente — el backend no lo proxea.
"""

from __future__ import annotations

import os
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

# Agregar raíz del proyecto al path para que `src.*` y `api.*` sean importables.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response

from api.routes import (
    admin_router,
    auth_router,
    excel_import_router,
    health_router,
    models_router,
    projects_router,
)
from src.db import motor_client
from src.logger import configure_logging, get_logger

configure_logging()
log = get_logger("api.main")


# ─── Lifespan ───────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa los singletons del proceso al arrancar."""
    # ── Cosmos DB (Motor async) ──────────────────────────────────────────
    # Opcional: si COSMOS_CONNECTION_STRING no está, la API arranca igual
    # pero los endpoints que accedan a DB fallarán con 500.
    try:
        await motor_client.connect()
        app.state.db_connected = True
        log.info("motor db connected")
    except Exception as e:
        app.state.db_connected = False
        log.error("motor db connection failed", extra={"error": str(e)})

    log.info("api started")
    try:
        yield
    finally:
        log.info("api stopping")
        await motor_client.disconnect()


# ─── Aplicación ─────────────────────────────────────────────────────────


app = FastAPI(
    title="Data Modeler Platform Backend",
    description=(
        "API REST de plataforma del Data Modeler. Maneja autenticación, "
        "permisos, proyectos, modelos y la persistencia en Cosmos DB. "
        "El agente de modelado conversacional vive en `app-agents-modeler`."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# ─── CORS ───────────────────────────────────────────────────────────────
# Política: allowlist explícita; `allow_credentials=True` porque el
# frontend Next.js envía la cookie de sesión `modeler-auth` con cada
# request al backend.

_cors_origins_env = os.getenv("CORS_ORIGINS", "").strip()
if _cors_origins_env:
    _cors_origins = [o.strip() for o in _cors_origins_env.split(",") if o.strip()]
else:
    _cors_origins = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Middleware: logueo de requests ─────────────────────────────────────


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next) -> Response:
    """Loguea inicio/fin de cada request con su request_id y duración."""
    request_id = uuid.uuid4().hex[:12]
    started = time.monotonic()

    log.info(
        "request started",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
        },
    )
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        log.exception(
            "request crashed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "ms": elapsed_ms,
            },
        )
        raise

    elapsed_ms = int((time.monotonic() - started) * 1000)
    log.info(
        "request completed",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "ms": elapsed_ms,
        },
    )
    response.headers["X-Request-ID"] = request_id
    return response


# ─── Montaje de routers ─────────────────────────────────────────────────

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(projects_router)
app.include_router(models_router)
app.include_router(excel_import_router)
app.include_router(admin_router)


# ─── Ejecución directa (desarrollo) ─────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
