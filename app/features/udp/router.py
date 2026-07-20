"""Endpoints de UDP (User Defined Properties). Solo LECTURA de definiciones —
las mutaciones son versionadas vía `POST /api/standards/apply` (udpUpsert/
udpDelete), como Glossary y Parent Domains. La lectura queda abierta (la usan el
panel de Properties y el módulo Data Standards)."""
from __future__ import annotations

from fastapi import APIRouter

from app.core.api.envelope import ok

from . import service

router = APIRouter(prefix="/api/udp", tags=["udp"])


@router.get("")
async def list_udp():
    """Definiciones UDP activas (keys con tipo, default, allowedValues, appliesTo)."""
    return ok(await service.list_udp())
