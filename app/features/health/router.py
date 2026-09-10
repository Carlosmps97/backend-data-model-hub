"""Endpoint de salud del backend de plataforma.

GET /api/health → estado de la API y de la conexión a la base de datos.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.core.config import settings

router = APIRouter(prefix="/api", tags=["health"])


class BuildInfo(BaseModel):
    """Qué código está corriendo (doc 82): el workflow de deploy estampa el
    commit y la hora del deploy en `BUILD_SHA`/`BUILD_TIME`. Sin ellos (dev
    local) es `None`. Con esto se distingue «el fix no funciona» de «el fix
    todavía no está desplegado» — el 2026-09-09 un 500 ya corregido en el
    repo se reportó como vivo porque la app corría el commit anterior."""

    sha: str
    time: str | None = None


class HealthResponse(BaseModel):
    """Respuesta de GET /api/health."""

    status: str
    version: str
    db_connected: bool
    build: BuildInfo | None = None


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Estado de la API y conexión con la base de datos.",
)
async def health_check(request: Request) -> HealthResponse:
    # Ping VIVO con timeout corto: el booleano del arranque quedaba congelado
    # (una caída de la BD posterior seguía reportando healthy).
    db_connected = bool(getattr(request.app.state, "db_connected", False))
    if db_connected:
        try:
            from app.core.db.client import get_db

            db = await get_db()
            await asyncio.wait_for(db.command("ping"), timeout=2.0)
        except Exception:  # noqa: BLE001
            db_connected = False
    return HealthResponse(
        status="ok" if db_connected else "degraded",
        version=getattr(request.app, "version", "1.0.0"),
        db_connected=db_connected,
        build=build_info(),
    )


def build_info() -> BuildInfo | None:
    """`BUILD_SHA` (+ `BUILD_TIME`) del entorno, o None si no se estamparon."""
    sha = (settings.BUILD_SHA or "").strip()
    if not sha:
        return None
    return BuildInfo(sha=sha, time=(settings.BUILD_TIME or "").strip() or None)
