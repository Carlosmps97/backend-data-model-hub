"""Endpoint de salud del backend de plataforma.

GET /api/health → estado de la API y de la conexión a la base de datos.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(prefix="/api", tags=["health"])


class HealthResponse(BaseModel):
    """Respuesta de GET /api/health."""

    status: str
    version: str
    db_connected: bool


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Estado de la API y conexión con la base de datos.",
)
async def health_check(request: Request) -> HealthResponse:
    # Ping VIVO con timeout corto: el booleano del arranque quedaba congelado
    # (una caída de Cosmos posterior seguía reportando healthy).
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
    )
