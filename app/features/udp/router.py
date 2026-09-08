"""Endpoints de UDP (User Defined Properties). Solo LECTURA de definiciones —
las mutaciones son versionadas vía `POST /api/projects/{pid}/standards/apply`
(udpUpsert/udpDelete), como Glossary y Parent Domains. La lectura queda abierta
(la usan el panel de Properties y el módulo Data Standards). Doc 75 D3/D4:
prefijo `/api/projects/{project_id}/udp`."""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.api.envelope import ok
from app.features.projects.deps import alive_project

from . import service

router = APIRouter(prefix="/api/projects/{project_id}/udp", tags=["udp"])


@router.get("")
async def list_udp(project_id: str = Depends(alive_project)):
    """Definiciones UDP activas del proyecto (keys con tipo, default, allowedValues, appliesTo)."""
    return ok(await service.list_udp(project_id))
