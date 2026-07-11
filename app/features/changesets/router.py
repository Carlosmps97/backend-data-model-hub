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
from app.core.audit import audit
from app.core.identity import Principal, current_principal
from app.features.auth.deps import require_permission

from . import service

# Gates de permiso (RBAC): escribir el modelo exige `model.edit`; decidir un
# request (aprobar/rechazar/publicar) exige `review.decide`. Sin esto, cualquier
# sesión válida (incluso rol lector) podía crear/editar o publicar a producción.
_can_edit = require_permission("model.edit")
_can_decide = require_permission("review.decide")
from .repository import VERSIONED
from .validation import DuplicateEntityError, InvalidPayloadError
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
async def create(body: ChangesetCreate, user: dict = Depends(_can_edit)):
    return ok(await service.create(body.title, user["username"]))


@router.get("")
async def list_all():
    return ok(await service.list_all())


@router.post("/snapshot", status_code=status.HTTP_201_CREATED)
async def snapshot(body: SnapshotBody, user: dict = Depends(_can_edit)):
    """Crea un draft (working copy) a partir del estado publicado (Open model · snapshot)."""
    return ok(
        await service.snapshot(
            user["username"], body.title, body.description, body.projectIds, body.versionLabel
        )
    )


@router.get("/{cs_id}")
async def get(cs_id: str):
    return ok(await service.get(cs_id))


@router.put("/{cs_id}/changes")
async def add_change(cs_id: str, body: ChangeBody, user: dict = Depends(_can_edit)):
    if body.collection not in VERSIONED:
        # Una colección arbitraria acabaría APLICADA a Mongo en el publish
        # (apply_changes escribe en db[collection] literal): whitelist dura.
        raise HTTPException(status_code=422, detail=f"Colección no versionada: {body.collection!r}.")
    try:
        res = await service.add_change(cs_id, user["username"], body.collection, body.entityId, body.op, body.payload)
    except DuplicateEntityError as exc:
        # Unicidad de nombres (spec 10 §9): el Save queda bloqueado ACÁ, en el
        # router — el publish queda protegido transitivamente (y re-chequeado).
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except InvalidPayloadError as exc:
        # El upsert terminaría aplicado tal cual a la colección publicada:
        # payload que no valida contra el modelo NO entra al changeset.
        raise HTTPException(status_code=422, detail=f"Cambio inválido — {exc}") from exc
    if res == "forbidden":
        raise HTTPException(status_code=403, detail="Sólo el dueño de la versión puede editar su working copy.")
    if res == "locked":
        # Editar una versión que ya salió de draft NO es silencioso: 409 explícito.
        raise HTTPException(
            status_code=409,
            detail="La versión ya no está en borrador (fue enviada a revisión o cerrada): "
                   "no admite más cambios. Abrí una nueva versión para editar.",
        )
    return ok(res)


@router.get("/{cs_id}/effective/{collection}")
async def effective(
    cs_id: str,
    collection: str,
    tableId: str | None = Query(default=None),
    ids: str | None = Query(default=None, description="ids separados por coma"),
    q: str | None = Query(default=None, description="búsqueda por nombre (contains, case-insensitive)"),
    limit: int | None = Query(default=None, ge=1, le=500),
):
    """Estado efectivo de una colección. `tableId`/`ids` acotan la respuesta a
    un slice — obligatorio en colecciones grandes (canonical_columns).
    `q`+`limit`: búsqueda server-side por nombre (modales de catálogo)."""
    if collection not in VERSIONED:
        raise HTTPException(status_code=422, detail=f"Colección no versionada: {collection!r}.")
    id_list = [s for s in (ids.split(",") if ids else []) if s] or None
    return ok(await service.effective(cs_id, collection, table_id=tableId, ids=id_list, q=q, limit=limit))


@router.get("/{cs_id}/diff")
async def diff(cs_id: str):
    """Diff estructurado por colección (added/edited/deleted con nombres) + impacto (p5)."""
    return ok(await service.diff(cs_id))


@router.post("/{cs_id}/submit")
async def submit(cs_id: str, body: SubmitBody | None = None,
                 user: dict = Depends(_can_edit)):
    """Publish request: asigna revisores/título/descripción y pasa a `submitted`.
    Owner-only y SÓLO desde draft (un request en revisión se retira primero con
    withdraw — re-submitir pisaría el envío anterior). Body opcional para compat
    con el submit M-series (sin revisores)."""
    body = body or SubmitBody()
    if not body.reviewers:
        # Sin revisores el request queda 'submitted' pero inactivable (la
        # unanimidad de [] nunca se cumple): exigir al menos uno.
        raise HTTPException(status_code=400, detail="Asigná al menos un revisor para enviar a revisión.")
    res = await service.submit(cs_id, user["username"], body.title, body.description,
                               body.reviewers, body.projectIds)
    if res == "forbidden":
        raise HTTPException(status_code=403, detail="Sólo el dueño de la versión puede enviarla a revisión.")
    if res is None:
        raise HTTPException(
            status_code=409,
            detail="La versión no está en borrador: no se puede enviar a revisión "
                   "(si ya está en revisión, retirala primero con Withdraw).",
        )
    await audit(user["username"], "changeset.submit", target=cs_id, target_type="changeset",
                meta={"reviewers": body.reviewers})
    return ok(res)


async def _decide(cs_id: str, actor: str, decision: str, note: str | None):
    """Registra la decisión de un revisor asignado vía `service.review` (política
    de UNANIMIDAD: sólo se aplica cuando TODOS los revisores aprobaron). Camino
    único de decisión — /review, /approve y /reject pasan por acá."""
    try:
        res = await service.review(cs_id, actor, decision, note)
    except DuplicateEntityError as exc:
        # Carrera entre changesets (spec 10 §9): otro publish ganó el nombre.
        # El claim ya se revirtió (producción intacta); el request sigue en revisión.
        raise HTTPException(
            status_code=409,
            detail=f"No se pudo publicar: {exc}. El owner debe retirar la versión (Withdraw), corregir y re-enviar.",
        ) from exc
    except InvalidPayloadError as exc:
        # Gate autoritativo del apply: el claim se revirtió, producción intacta.
        raise HTTPException(
            status_code=422,
            detail=f"No se pudo publicar: {exc}. El owner debe retirar la versión (Withdraw), corregir y re-enviar.",
        ) from exc
    if res == "forbidden":
        raise HTTPException(status_code=403, detail="No estás asignado como revisor de este request.")
    if res is None:
        # ok(None) con 200 era un éxito FALSO (toast "approved" sobre un request
        # retirado). La carrera con withdraw ahora es un flujo de primera clase.
        raise HTTPException(
            status_code=409,
            detail="El request ya no está en revisión (fue retirado o ya decidido): recargá la lista.",
        )
    await audit(actor, "changeset.decide", target=cs_id, target_type="changeset",
                meta={"decision": decision, "result": res.get("status") if isinstance(res, dict) else None})
    return ok(res)


@router.post("/{cs_id}/review")
async def review(cs_id: str, body: ReviewDecisionBody, user: dict = Depends(_can_decide)):
    """Decisión (approve/reject) de UN revisor asignado. Exige permiso
    `review.decide` y estar asignado (403 si no); aplica sólo con unanimidad."""
    return await _decide(cs_id, user["username"], body.decision, body.note)


@router.post("/{cs_id}/withdraw")
async def withdraw(cs_id: str, user: dict = Depends(_can_edit)):
    """Retira un request en revisión (submitted → draft) para seguir editándolo.
    Sólo el owner; las decisiones registradas se invalidan."""
    res = await service.withdraw(cs_id, user["username"])
    if res == "forbidden":
        raise HTTPException(status_code=403, detail="Sólo quien envió el request puede retirarlo.")
    if res is None:
        raise HTTPException(status_code=409, detail="El request ya no está en revisión (quizá ya fue decidido).")
    await audit(user["username"], "changeset.withdraw", target=cs_id, target_type="changeset")
    return ok(res)


@router.post("/{cs_id}/reopen")
async def reopen(cs_id: str, user: dict = Depends(_can_edit)):
    """Reabre un request rechazado (`rejected → draft`) para corregir y re-enviar.
    Sólo el owner; el reject nunca elimina la versión."""
    res = await service.reopen(cs_id, user["username"])
    if res == "forbidden":
        raise HTTPException(status_code=403, detail="Sólo el dueño de la versión puede reabrirla.")
    if res is None:
        raise HTTPException(status_code=409, detail="La versión no está rechazada: no hay nada que reabrir.")
    await audit(user["username"], "changeset.reopen", target=cs_id, target_type="changeset")
    return ok(res)


@router.post("/{cs_id}/comments")
async def add_comment(cs_id: str, body: CommentBody, principal: Principal = Depends(current_principal)):
    return ok(await service.add_comment(cs_id, principal.username, body.text))


# ── Compat M-series: approve/reject ahora DELEGAN en la misma política de
#    revisión (unanimidad + is_assigned). Antes /approve aplicaba con UNA sola
#    aprobación, salteando la unanimidad que /review sí exige.


@router.post("/{cs_id}/reject")
async def reject(cs_id: str, body: ReviewBody, user: dict = Depends(_can_decide)):
    return await _decide(cs_id, user["username"], "reject", body.note)


@router.post("/{cs_id}/approve")
async def approve(cs_id: str, user: dict = Depends(_can_decide)):
    return await _decide(cs_id, user["username"], "approve", None)


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
