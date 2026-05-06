"""API REST async para el Data Modeler Agent.

Punto de entrada de FastAPI. Responsabilidades acotadas:
- Configurar logging.
- Lifespan: crear singletons (FoundryChatClient, ConversationStore,
  asyncio.Semaphore) y guardarlos en `app.state`.
- Middleware: logueo estructurado de cada request.
- CORS para el frontend Next.js.
- Montar los routers `health`, `conversations`, `modeling`.

Sin lógica de negocio.
"""

from __future__ import annotations

import asyncio
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
    auth_router,
    conversations_router,
    health_router,
    modeling_router,
    models_router,
    projects_router,
)
from src.agents.factory import get_chat_client
from src.conversation import ConversationStore
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

    # ── ConversationStore (en memoria) ───────────────────────────────────
    app.state.store = ConversationStore()

    # ── Cliente de AI Foundry ────────────────────────────────────────────
    # Opcional al arranque: los endpoints que lo requieren responden 503 si None.
    try:
        app.state.chat_client = get_chat_client()
        log.info("foundry connected")
    except Exception as e:
        app.state.chat_client = None
        log.error("foundry connection failed", extra={"error": str(e)})

    # ── Semaphore de pipeline ────────────────────────────────────────────
    try:
        max_concurrent = int(os.getenv("MAX_CONCURRENT_PIPELINES", "5"))
    except ValueError:
        max_concurrent = 5
    max_concurrent = max(1, max_concurrent)
    app.state.pipeline_semaphore = asyncio.Semaphore(max_concurrent)
    log.info(
        "pipeline semaphore initialized",
        extra={"max_concurrent": max_concurrent},
    )

    log.info("api started")
    try:
        yield
    finally:
        log.info("api stopping")
        await motor_client.disconnect()


# ─── Aplicación ─────────────────────────────────────────────────────────


app = FastAPI(
    title="Data Modeler Agent API",
    description=(
        "API REST para generación de modelos de datos con validación QA. "
        "Soporta conversaciones multi-turno con memoria por `conversation_id`."
    ),
    version="2.0.0",
    lifespan=lifespan,
)


# ─── CORS ───────────────────────────────────────────────────────────────

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
    """Loguea inicio/fin de cada request con conv id (si está) y duración."""
    request_id = uuid.uuid4().hex[:12]
    started = time.monotonic()
    conv_id = _extract_conversation_id(request.url.path)

    log.info(
        "request started",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "conversation_id": conv_id,
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
                "conversation_id": conv_id,
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
            "conversation_id": conv_id,
            "status": response.status_code,
            "ms": elapsed_ms,
        },
    )
    response.headers["X-Request-ID"] = request_id
    return response


def _extract_conversation_id(path: str) -> str | None:
    """Extrae el `conversation_id` del path si está presente."""
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 3 and parts[0] == "api" and parts[1] == "conversations":
        return parts[2]
    return None


# ─── Montaje de routers ─────────────────────────────────────────────────

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(conversations_router)
app.include_router(modeling_router)
app.include_router(projects_router)
app.include_router(models_router)


# ─── Ejecución directa (desarrollo) ─────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "api.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
