"""Lógica de negocio del catálogo `metadata` (Semantic Types / UDPs).

Lecturas abiertas a cualquier usuario auth; escrituras requieren rol no-viewer
(el gating fino lo aplica el router con `EditorDep`).
"""

from __future__ import annotations

from fastapi import HTTPException, status

from . import repository as repo
from .schemas import SemanticTypeBody, UdpBody


# ── Semantic Types ───────────────────────────────────────────────────────────

async def list_semantic_types() -> list[dict]:
    return await repo.list_semantic_types()


async def create_semantic_type(body: SemanticTypeBody) -> dict:
    if not body.name.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name is required.")
    return await repo.create_semantic_type(body.model_dump())


async def update_semantic_type(st_id: str, body: SemanticTypeBody) -> dict:
    updated = await repo.update_semantic_type(st_id, body.model_dump())
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Semantic Type not found.")
    return updated


async def delete_semantic_type(st_id: str) -> dict:
    if not await repo.delete_semantic_type(st_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Semantic Type not found.")
    return {"deleted": True}


# ── UDPs ─────────────────────────────────────────────────────────────────────

async def list_udps() -> list[dict]:
    return await repo.list_udps()


async def create_udp(body: UdpBody) -> dict:
    if not body.name.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Name is required.")
    return await repo.create_udp(body.model_dump())


async def update_udp(udp_id: str, body: UdpBody) -> dict:
    updated = await repo.update_udp(udp_id, body.model_dump())
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="UDP not found.")
    return updated


async def delete_udp(udp_id: str) -> dict:
    if not await repo.delete_udp(udp_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="UDP not found.")
    return {"deleted": True}
