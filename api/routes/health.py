"""Endpoint de salud del backend de plataforma.

GET /api/health → estado de la API y de la conexión a Cosmos DB.

El endpoint `/api/engines` y los flags relacionados con el agente
(`foundry_connected`, `default_engine`, `active_conversations`) viven
en el servicio `app-agents-modeler`.
"""

from __future__ import annotations

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
    summary="Estado de la API y conexión con Cosmos DB.",
)
async def health_check(request: Request) -> HealthResponse:
    db_connected = bool(getattr(request.app.state, "db_connected", False))
    return HealthResponse(
        status="ok",
        version=getattr(request.app, "version", "1.0.0"),
        db_connected=db_connected,
    )
