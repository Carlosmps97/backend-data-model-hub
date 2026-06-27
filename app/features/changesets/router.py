"""Endpoints de changesets (working copy + flujo de aprobación) + routers
hermanos de **versiones** (p5) y **requests** (Home/Review), cross-project.

Se preservan los endpoints M-series (create/list/get/changes/effective/submit/
reject/approve) que el frontend ya consume, y se agregan los de R1b: snapshot,
review por revisor asignado (gate 403), comentarios y diff enriquecido.
Las rutas estáticas (`/snapshot`) se declaran antes de `/{cs_id}`.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.api.envelope import ok
from app.core.identity import Principal, current_principal

from . import service
from .schemas import (
    ChangeBody,
    ChangesetCreate,
    CommentBody,
    ReviewBody,
    ReviewDecisionBody,
    SnapshotBody,
    SubmitBody,
)

router = APIRouter(prefix="/api/changesets", tags=["changesets"])


@router.post("", status_code=status.HTTP_201_CREATED)
async def create(body: ChangesetCreate, principal: Principal = Depends(current_principal)):
    return ok(await service.create(body.title, principal.username))


@router.get("")
async def list_all():
    return ok(await service.list_all())


@router.post("/snapshot", status_code=status.HTTP_201_CREATED)
async def snapshot(body: SnapshotBody, principal: Principal = Depends(current_principal)):
    """Crea un draft (working copy) a partir del estado publicado (Open model · snapshot)."""
    return ok(
        await service.snapshot(
            principal.username, body.title, body.description, body.projectIds, body.versionLabel
        )
    )


@router.get("/{cs_id}")
async def get(cs_id: str):
    return ok(await service.get(cs_id))


@router.put("/{cs_id}/changes")
async def add_change(cs_id: str, body: ChangeBody):
    return ok(await service.add_change(cs_id, body.collection, body.entityId, body.op, body.payload))


@router.get("/{cs_id}/effective/{collection}")
async def effective(cs_id: str, collection: str):
    return ok(await service.effective(cs_id, collection))


@router.get("/{cs_id}/diff")
async def diff(cs_id: str):
    """Diff estructurado por colección (added/edited/deleted con nombres) + impacto (p5)."""
    return ok(await service.diff(cs_id))


@router.post("/{cs_id}/submit")
async def submit(cs_id: str, body: SubmitBody | None = None):
    """Publish request: asigna revisores/título/descripción y pasa a `submitted`.
    Body opcional para compat con el submit M-series (sin revisores)."""
    body = body or SubmitBody()
    return ok(await service.submit(cs_id, body.title, body.description, body.reviewers, body.projectIds))


@router.post("/{cs_id}/review")
async def review(cs_id: str, body: ReviewDecisionBody, principal: Principal = Depends(current_principal)):
    """Decisión (approve/reject) de UN revisor asignado. 403 si el actor no está asignado."""
    res = await service.review(cs_id, principal.username, body.decision, body.note)
    if res == "forbidden":
        raise HTTPException(status_code=403, detail="No estás asignado como revisor de este request.")
    return ok(res)


@router.post("/{cs_id}/comments")
async def add_comment(cs_id: str, body: CommentBody, principal: Principal = Depends(current_principal)):
    return ok(await service.add_comment(cs_id, principal.username, body.text))


# ── Compat M-series: approve/reject directos (sin política de reviewers) ───


@router.post("/{cs_id}/reject")
async def reject(cs_id: str, body: ReviewBody, principal: Principal = Depends(current_principal)):
    return ok(await service.reject(cs_id, principal.username, body.note))


@router.post("/{cs_id}/approve")
async def approve(cs_id: str, principal: Principal = Depends(current_principal)):
    return ok(await service.approve(cs_id, principal.username))


# ── Routers hermanos: versiones (p5) y requests (Home / Review) ───────────

versions_router = APIRouter(prefix="/api/versions", tags=["versions"])


@versions_router.get("")
async def list_versions():
    """Lista cross-project de versiones (filas para la tabla de Review & publish)."""
    return ok(await service.list_versions())


@versions_router.get("/published")
async def current_production():
    """Versión de producción actual (la última approved/aplicada — fila verde)."""
    return ok(await service.current_production())


requests_router = APIRouter(prefix="/api/requests", tags=["requests"])


@requests_router.get("")
async def list_requests(
    reviewer: str | None = Query(default=None),
    owner: str | None = Query(default=None),
):
    """Publish requests en revisión. `reviewer` = asignado (p.ej. el actor);
    `owner` = solicitante. Sin filtros, todos los `submitted`."""
    return ok(await service.list_requests(reviewer, owner))
