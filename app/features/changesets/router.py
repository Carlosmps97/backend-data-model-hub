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
from app.features.auth import repository as auth_repo
from app.features.auth.deps import require_permission

from . import service

# Gates de permiso (RBAC): escribir el modelo exige `model.edit`; decidir un
# request (aprobar/rechazar/publicar) exige `review.decide`. Sin esto, cualquier
# sesión válida (incluso rol lector) podía crear/editar o publicar a producción.
_can_edit = require_permission("model.edit")
_can_decide = require_permission("review.decide")
# Rollback a una versión publicada = permiso propio (afecta producción, como publish).
_can_rollback = require_permission("rollback")
from .repository import VERSIONED
from .validation import DuplicateEntityError, InvalidPayloadError, NameTooLongError, SchemaInUseError
from .schemas import (
    ChangeBody,
    ChangesetCreate,
    CommentBody,
    DiffDetailsBody,
    ReviewBody,
    ReviewDecisionBody,
    SchemaRenameBody,
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
        raise HTTPException(status_code=422, detail=f"Collection not under versioning: {body.collection!r}.")
    try:
        res = await service.add_change(cs_id, user["username"], body.collection, body.entityId, body.op, body.payload)
    except DuplicateEntityError as exc:
        # Unicidad de nombres (spec 10 §9): el Save queda bloqueado ACÁ, en el
        # router — el publish queda protegido transitivamente (y re-chequeado).
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except NameTooLongError as exc:
        # Nombre físico sobre el límite del naming config (Data Standards): 400
        # con el mensaje legible; el create popup lo muestra inline.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InvalidPayloadError as exc:
        # El upsert terminaría aplicado tal cual a la colección publicada:
        # payload que no valida contra el modelo NO entra al changeset.
        raise HTTPException(status_code=422, detail=f"Invalid change — {exc}") from exc
    if res == "forbidden":
        raise HTTPException(status_code=403, detail="Only the version owner can edit its working copy.")
    if res == "locked":
        # Editar una versión que ya salió de draft NO es silencioso: 409 explícito.
        raise HTTPException(
            status_code=409,
            detail="The version is no longer in draft (it was sent to review or closed): "
                   "it doesn't accept more changes. Open a new version to edit.",
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
    schema: str | None = Query(default=None, description="tablas/vistas de un esquema (Database Explorer)"),
):
    """Estado efectivo de una colección. `tableId`/`ids` acotan la respuesta a
    un slice — obligatorio en colecciones grandes (canonical_columns).
    `q`+`limit`: búsqueda server-side por nombre (modales de catálogo).
    `schema`: tablas/vistas de UN esquema, draft-aware (Database Explorer)."""
    if collection not in VERSIONED:
        raise HTTPException(status_code=422, detail=f"Collection not under versioning: {collection!r}.")
    id_list = [s for s in (ids.split(",") if ids else []) if s] or None
    return ok(await service.effective(cs_id, collection, table_id=tableId, ids=id_list,
                                      q=q, limit=limit, schema=schema))


@router.get("/{cs_id}/diff")
async def diff(cs_id: str):
    """Diff estructurado por colección (added/edited/deleted con nombres) + impacto (p5)."""
    return ok(await service.diff(cs_id))


@router.post("/{cs_id}/diff/details")
async def diff_details(cs_id: str, body: DiffDetailsBody):
    """Diff de campos ANTES→DESPUÉS de entidades puntuales del changeset
    (doc 31): alimenta el popup "Change details" de la revisión. READ-ONLY;
    las entidades que no pertenecen al changeset se omiten de la respuesta."""
    bad = sorted({i.collection for i in body.items} - set(VERSIONED))
    if bad:
        raise HTTPException(status_code=400,
                            detail=f"Unknown collection(s): {', '.join(bad)}.")
    res = await service.diff_details(cs_id, [(i.collection, i.entityId) for i in body.items])
    if res is None:
        raise HTTPException(status_code=404, detail="That changeset doesn't exist.")
    return ok(res)


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
        raise HTTPException(status_code=400, detail="Assign at least one reviewer to send to review.")
    # Gate de asignación: un revisor cuyo rol NO tiene `review.decide` jamás
    # podría votar → el request quedaría trabado (unanimidad imposible) y ese
    # revisor vería un 403 confuso al intentar aprobar. Se corta acá con 400.
    users_by_id = {u["id"]: u for u in await auth_repo.list_users()}
    perms_by_role = {r["id"]: (r.get("permissions") or {}) for r in await auth_repo.list_roles()}
    bad = [r for r in body.reviewers
           if not perms_by_role.get((users_by_id.get(r) or {}).get("role") or "", {}).get("review.decide")]
    if bad:
        names = ", ".join((users_by_id.get(b) or {}).get("name") or b for b in bad)
        raise HTTPException(
            status_code=400,
            detail=f"Reviewer(s) without approval permission (review.decide): {names}. "
                   "Assign users with the reviewer or administrator role.",
        )
    res = await service.submit(cs_id, user["username"], body.title, body.description,
                               body.reviewers, body.projectIds)
    if res == "forbidden":
        raise HTTPException(status_code=403, detail="Only the version owner can send it to review.")
    if res is None:
        raise HTTPException(
            status_code=409,
            detail="The version is not in draft: it can't be sent to review "
                   "(if it's already in review, withdraw it first).",
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
            detail=f"Publish failed: {exc}. The owner must withdraw the version, fix it and re-submit.",
        ) from exc
    except SchemaInUseError as exc:
        # Delete de esquema con tablas/vistas efectivas (doc 18): el claim ya
        # se revirtió, producción intacta; el request sigue en revisión.
        raise HTTPException(
            status_code=409,
            detail=f"Publish failed: {exc}. The owner must withdraw the version, fix it and re-submit.",
        ) from exc
    except InvalidPayloadError as exc:
        # Gate autoritativo del apply: el claim se revirtió, producción intacta.
        raise HTTPException(
            status_code=422,
            detail=f"Publish failed: {exc}. The owner must withdraw the version, fix it and re-submit.",
        ) from exc
    if res == "forbidden":
        raise HTTPException(status_code=403, detail="You are not assigned as a reviewer of this request.")
    if res is None:
        # ok(None) con 200 era un éxito FALSO (toast "approved" sobre un request
        # retirado). La carrera con withdraw ahora es un flujo de primera clase.
        raise HTTPException(
            status_code=409,
            detail="The request is no longer in review (it was withdrawn or already decided): reload the list.",
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
        raise HTTPException(status_code=403, detail="Only whoever submitted the request can withdraw it.")
    if res is None:
        raise HTTPException(status_code=409, detail="The request is no longer in review (it may have already been decided).")
    await audit(user["username"], "changeset.withdraw", target=cs_id, target_type="changeset")
    return ok(res)


@router.post("/{cs_id}/reopen")
async def reopen(cs_id: str, user: dict = Depends(_can_edit)):
    """Reabre un request rechazado (`rejected → draft`) para corregir y re-enviar.
    Sólo el owner; el reject nunca elimina la versión."""
    res = await service.reopen(cs_id, user["username"])
    if res == "forbidden":
        raise HTTPException(status_code=403, detail="Only the version owner can reopen it.")
    if res is None:
        raise HTTPException(status_code=409, detail="The version is not rejected: there is nothing to reopen.")
    await audit(user["username"], "changeset.reopen", target=cs_id, target_type="changeset")
    return ok(res)


@router.post("/{cs_id}/rollback")
async def rollback(cs_id: str, user: dict = Depends(_can_rollback)):
    """Rollback a CUALQUIER versión publicada: crea un DRAFT que restaura el
    modelo al estado de esa versión (deshace las versiones posteriores). El
    draft pasa por el flujo normal — submit → review → approve — así el
    rollback también se revisa, se audita y se re-valida antes de tocar
    producción (mismo estándar que Data Standards, pero con governance).
    Requiere el permiso `rollback`."""
    res = await service.rollback(cs_id, user["username"])
    if res is None:
        raise HTTPException(status_code=404, detail="Version not found.")
    if res == "not-applied":
        raise HTTPException(status_code=409, detail="Only a PUBLISHED version can be rolled back.")
    if res == "no-before":
        raise HTTPException(status_code=409,
                            detail="A version published after this one predates rollback support "
                                   "(its before-images were not captured), so this rollback can't be reconstructed.")
    if res == "empty":
        raise HTTPException(status_code=409,
                            detail="This is already the current production version — nothing after it to undo.")
    await audit(user["username"], "changeset.rollback_draft", target=cs_id, target_type="changeset",
                meta={"draft": res["id"]})
    return ok(res)


# ── Entidad `schemas` dentro del draft (doc 18): rename con propagación y
#    delete con guard de uso — server-side (el effective de tablas es enorme
#    para el cliente y ambos deben ser atómicos respecto del draft). ──


def _schema_change_result(res, cs_id: str):
    """Mapea la semántica de add_change de los endpoints de esquema a HTTP."""
    if res == "forbidden":
        raise HTTPException(status_code=403, detail="Only the version owner can edit its working copy.")
    if res == "locked":
        raise HTTPException(
            status_code=409,
            detail="The version is no longer in draft (it was sent to review or closed): "
                   "it doesn't accept more changes.",
        )
    if res is None:
        raise HTTPException(status_code=404, detail="Version or schema not found.")
    return res


@router.get("/{cs_id}/schemas/{schema_id}/impact")
async def schema_impact(cs_id: str, schema_id: str):
    """Impacto EFECTIVO de tocar el esquema: {name, tables, views, canvases}.
    Lo consume el gestor de esquemas ANTES de guardar un rename/delete."""
    res = await service.schema_impact(cs_id, schema_id)
    if res is None:
        raise HTTPException(status_code=404, detail="Version or schema not found.")
    return ok(res)


@router.post("/{cs_id}/schemas/{schema_id}/rename")
async def rename_schema(cs_id: str, schema_id: str, body: SchemaRenameBody,
                        user: dict = Depends(_can_edit)):
    """Renombra un esquema DENTRO del draft: registra el schema + un upsert por
    cada tabla/vista efectiva que lo usa (docs completos, invariante overlay).
    Devuelve {tables, views} con lo propagado."""
    try:
        res = await service.rename_schema(cs_id, user["username"], schema_id, body.newName)
    except InvalidPayloadError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except DuplicateEntityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    counts = _schema_change_result(res, cs_id)
    await audit(user["username"], "changeset.schema_rename", target=cs_id, target_type="changeset",
                meta={"schemaId": schema_id, "newName": body.newName, **counts})
    return ok(counts)


@router.post("/{cs_id}/schemas/{schema_id}/delete")
async def delete_schema_in_cs(cs_id: str, schema_id: str, user: dict = Depends(_can_edit)):
    """Registra el delete del esquema en el draft, sólo si su estado efectivo
    (publicado + este draft) no tiene tablas ni vistas."""
    res = await service.delete_schema_in_changeset(cs_id, user["username"], schema_id)
    if isinstance(res, tuple) and res[0] == "in-use":
        raise HTTPException(
            status_code=409,
            detail=f"The schema still has {res[1]} table(s)/view(s): it can't be deleted.",
        )
    _schema_change_result(res, cs_id)
    await audit(user["username"], "changeset.schema_delete", target=cs_id, target_type="changeset",
                meta={"schemaId": schema_id})
    return ok({"deleted": True})


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
