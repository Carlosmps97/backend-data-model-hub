"""Endpoints de salud y descubrimiento.

- GET /api/health: estado general de la API.
- GET /api/engines: motores de BD soportados.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from src.api.dependencies import StoreDep
from src.config import settings
from src.schemas import EnginesResponseAPI, HealthResponseAPI

router = APIRouter(prefix="/api", tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponseAPI,
    summary="Estado de la API y conexión con Azure AI Foundry.",
)
async def health_check(request: Request, store: StoreDep) -> HealthResponseAPI:
    chat_client = getattr(request.app.state, "chat_client", None)
    active = await store.list_ids()
    return HealthResponseAPI(
        status="ok",
        version=getattr(request.app, "version", "1.0.0"),
        foundry_connected=chat_client is not None,
        default_engine=settings.DEFAULT_DB_ENGINE,
        active_conversations=len(active),
    )


@router.get(
    "/engines",
    response_model=EnginesResponseAPI,
    summary="Lista los motores de BD soportados.",
)
async def list_engines() -> EnginesResponseAPI:
    return EnginesResponseAPI(
        engines=settings.SUPPORTED_ENGINES,
        default=settings.DEFAULT_DB_ENGINE,
    )
