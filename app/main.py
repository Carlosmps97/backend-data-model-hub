"""Composition root del backend de plataforma (backend-data-model-hub).

Construye la app FastAPI y monta los routers de cada feature. Responsabilidades
acotadas:
- Configurar logging.
- Lifespan: abrir/cerrar la conexión a Cosmos DB (Motor async).
- Middleware: logueo estructurado de cada request.
- CORS para el frontend Next.js (con credenciales — comparte cookie
  `modeler-auth`).
- Montar los routers de `health`, `auth`, `projects`, `canvas`, `excel_import`,
  `metadata`, `users`.

El servicio de agentes vive en `app-agents-modeler` y se invoca desde el
frontend directamente — el backend no lo proxea.
"""

from __future__ import annotations

import os
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import Response

from app.core.db import client as db_client
from app.core.logging import configure_logging, get_logger
from app.features.auth.router import router as auth_router
from app.features.canvas.router import router as canvas_router
from app.features.excel_import.router import router as excel_import_router
from app.features.health.router import router as health_router
from app.features.metadata.router import router as metadata_router
from app.features.projects.router import router as projects_router
from app.features.users.router import router as users_router

configure_logging()
log = get_logger("app.main")


# ─── Lifespan ───────────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Inicializa los singletons del proceso al arrancar."""
    try:
        await db_client.connect()
        app.state.db_connected = True
        log.info("motor db connected")
    except Exception as e:  # noqa: BLE001
        app.state.db_connected = False
        log.error("motor db connection failed", extra={"error": str(e)})

    log.info("api started")
    try:
        yield
    finally:
        log.info("api stopping")
        await db_client.disconnect()


# ─── Aplicación ─────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    """Factory de la app FastAPI (CORS + middleware + routers de features)."""
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

    # ─── CORS ───────────────────────────────────────────────────────────
    # Allowlist explícita; `allow_credentials=True` porque el frontend envía
    # la cookie de sesión `modeler-auth` con cada request.
    cors_env = os.getenv("CORS_ORIGINS", "").strip()
    cors_origins = (
        [o.strip() for o in cors_env.split(",") if o.strip()]
        if cors_env
        else ["http://localhost:3000", "http://127.0.0.1:3000"]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ─── Middleware: logueo de requests ─────────────────────────────────
    @app.middleware("http")
    async def request_logging_middleware(request: Request, call_next) -> Response:
        request_id = uuid.uuid4().hex[:12]
        started = time.monotonic()
        log.info(
            "request started",
            extra={"request_id": request_id, "method": request.method, "path": request.url.path},
        )
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            log.exception(
                "request crashed",
                extra={"request_id": request_id, "method": request.method, "path": request.url.path, "ms": elapsed_ms},
            )
            raise
        elapsed_ms = int((time.monotonic() - started) * 1000)
        log.info(
            "request completed",
            extra={"request_id": request_id, "method": request.method, "path": request.url.path, "status": response.status_code, "ms": elapsed_ms},
        )
        response.headers["X-Request-ID"] = request_id
        return response

    # ─── Routers de features ────────────────────────────────────────────
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(projects_router)
    app.include_router(canvas_router)
    app.include_router(excel_import_router)
    app.include_router(metadata_router)
    app.include_router(users_router)

    return app


app = create_app()


# ─── Ejecución directa (desarrollo) ─────────────────────────────────────

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
