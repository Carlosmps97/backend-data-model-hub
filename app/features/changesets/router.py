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
from app.core.scope import ProjectDeletedError
from app.features.projects import repository as projects_repo
from app.features.projects.deps import alive_project
from app.core.audit import audit
from app.core.identity import Principal, current_principal
from app.features.auth import repository as auth_repo
from app.features.auth import service as auth_service
from app.features.auth.deps import require_permission
from app.features.changesets.access import GONE_DETAIL, ensure_changeset_visible

from . import service

# Gates de permiso (RBAC): escribir el modelo exige `model.edit`; decidir un
# request (aprobar/rechazar/publicar) exige `review.decide`. Sin esto, cualquier
# sesión válida (incluso rol lector) podía crear/editar o publicar a producción.
_can_edit = require_permission("model.edit")
_can_decide = require_permission("review.decide")
# Rollback a una versión publicada = permiso propio (afecta producción, como publish).
_can_rollback = require_permission("rollback")
from .repository import VERSIONED
from .validation import (
    CrossProjectError, DuplicateEntityError, InvalidPayloadError, NameTooLongError,
    PublishError, RelationshipKeyMismatchError, SchemaInUseError, TableInUseError)
from .schemas import (
    ChangeBody,
    ChangesBulkBody,
    CommentBody,
    CompareDetailsBody,
    DiffDetailsBody,
    ReviewBody,
    ReviewDecisionBody,
    SchemaRenameBody,
    SnapshotBody,
    SubmitBody,
    TransferBody,
)

router = APIRouter(prefix="/api/changesets", tags=["changesets"])


# `POST /api/changesets` (create M-series, sin proyecto) se RETIRÓ (doc 82):
# desde el doc 75 un changeset pertenece a UN proyecto y ese camino creaba la
# cabecera sin `projectId` → 500 en la validación del modelo. El único alta es
# `POST /api/changesets/snapshot {projectId}`; el front nunca usó el viejo.


@router.get("")
async def list_all():
    return ok(await service.list_all())


@router.post("/snapshot", status_code=status.HTTP_201_CREATED)
async def snapshot(body: SnapshotBody, user: dict = Depends(_can_edit)):
    """Crea un draft (working copy) DEL PROYECTO a partir de su estado publicado
    (Open model · snapshot). Doc 75 D2: 404 si el proyecto no existe o fue borrado."""
    if not await projects_repo.get_project(body.projectId):
        raise HTTPException(status_code=404, detail="Project not found.")
    return ok(
        await service.snapshot(
            user["username"], body.projectId, body.title, body.description, body.versionLabel
        )
    )


@router.get("/history/{collection}/{entity_id}")
async def entity_history(collection: str, entity_id: str,
                         limit: int = Query(default=50, ge=1, le=200)):
    """Historial de auditoría PUBLICADO de una entidad (doc 51): quién / cuándo /
    qué acción / qué versión / qué cambió, derivado en LECTURA del ledger de
    changesets aplicados (before-images del doc 16). Ruta estática — declarada
    antes de `/{cs_id}`. Solo tablas y columnas (alcance del pedido)."""
    if collection not in service.HISTORY_COLLECTIONS:
        raise HTTPException(status_code=422,
                            detail=f"History not available for collection: {collection!r}.")
    return ok(await service.entity_history(collection, entity_id, limit))


@router.get("/{cs_id}")
async def get(cs_id: str):
    # Doc 104: una versión eliminada responde 404 (antes 200 con `null`): el
    # canvas que la tenga abierta lo detecta y sale de ella con aviso.
    cs = await service.get(cs_id)
    if cs is None:
        raise HTTPException(status_code=404, detail=GONE_DETAIL)
    return ok(cs)


# ── Administración de versiones (doc 104): transferir / eliminar un draft ──

_ADMIN_ACTION_ERRORS = {
    "in-review": (409, "The version is in review: its owner must withdraw the request first "
                       "(or wait for the reviewers' decision)."),
    "uploading": (409, "An Excel upload is being written into this version right now. "
                       "Try again when it finishes."),
    "same-owner": (400, "That user already owns this version."),
    "bad-target": (400, "That user can't receive the version: the user doesn't exist or is disabled."),
    "target-cannot-edit": (400, "That user can't receive the version: their role can't edit models "
                                "(model.edit)."),
    "conflict": (409, "The version changed while you were doing this (it was sent to review, "
                      "transferred or deleted). Reload and try again."),
    "owner-cannot-edit": (403, "Your role can no longer edit models (model.edit): ask an administrator "
                               "to transfer or delete this version."),
    "partially-published": (409, "This version can't be deleted: an approval of it failed while it was being "
                                 "published, and some of its changes may already be in production. Send it "
                                 "for review again so the publish can complete."),
}


def _admin_action_result(res, verb: str):
    """Sentinelas de transferir/eliminar → HTTP con mensaje legible (`verb` =
    'transfer' | 'delete'). Devuelve el resultado OK."""
    past = {"transfer": "transferred", "delete": "deleted"}[verb]
    if res is None:
        raise HTTPException(status_code=404, detail=GONE_DETAIL)
    if res == "forbidden":
        raise HTTPException(status_code=403,
                            detail=f"Only the version owner or an administrator can {verb} it.")
    if res == "published":
        raise HTTPException(status_code=409,
                            detail=f"Published versions are part of the model history: they can't be {past}.")
    if res == "not-draft":
        raise HTTPException(status_code=409, detail=f"Only a version in draft can be {past}.")
    if isinstance(res, str):
        code, detail = _ADMIN_ACTION_ERRORS[res]
        raise HTTPException(status_code=code, detail=detail)
    return res


async def _session_user(principal: Principal) -> dict:
    """Usuario en sesión con permisos; 403 si no está registrado o está
    deshabilitado (mismo criterio que `require_permission`)."""
    user = await auth_service.resolve_session_user(principal.username)
    if user is None:
        raise HTTPException(status_code=403, detail="You don't have permission for this action.")
    return user


@router.post("/{cs_id}/transfer")
async def transfer(cs_id: str, body: TransferBody, principal: Principal = Depends(current_principal)):
    """Transfiere un DRAFT a otro usuario (doc 104): desde ahí ese usuario lo
    edita, lo envía y figura como autor al publicarse; la versión recuerda
    quién la inició (`transfers[]`). Owner (con `model.edit`) o administrador
    (`admin.manage`, también hacia sí mismo). Destino: usuario activo cuyo rol
    edita modelos."""
    user = await _session_user(principal)
    try:
        res = await service.transfer_version(cs_id, user["username"], user["permissions"],
                                             body.to, body.note, body.expectedOwner)
    except ProjectDeletedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    res = _admin_action_result(res, "transfer")
    last = (res.get("transfers") or [{}])[-1]
    await audit(user["username"], "changeset.transfer", target=cs_id, target_type="changeset",
                meta={"from": last.get("from"), "to": res.get("owner"), "note": last.get("note"),
                      "projectId": res.get("projectId"), "versionLabel": res.get("versionLabel")})
    return ok(res)


@router.delete("/{cs_id}")
async def delete_version(cs_id: str, expectedOwner: str | None = Query(default=None, max_length=320),
                         principal: Principal = Depends(current_principal)):
    """Elimina un DRAFT con todos sus cambios (doc 104). Nunca tocó
    producción; queda en la auditoría. Owner (con `model.edit`) o
    administrador. Una versión en revisión se retira primero; una publicada
    no se elimina jamás. `expectedOwner` = el dueño que mostraba la pantalla:
    si cambió en el medio, 409 (no se borra el draft de otra persona)."""
    user = await _session_user(principal)
    res = _admin_action_result(
        await service.delete_version(cs_id, user["username"], user["permissions"], expectedOwner), "delete")
    await audit(user["username"], "changeset.delete", target=cs_id, target_type="changeset",
                meta={k: res.get(k) for k in ("owner", "versionLabel", "title", "projectId", "changes")})
    return ok(res)


@router.put("/{cs_id}/changes")
async def add_change(cs_id: str, body: ChangeBody, user: dict = Depends(_can_edit)):
    if body.collection not in VERSIONED:
        # Una colección arbitraria acabaría APLICADA a Mongo en el publish
        # (apply_changes escribe en db[collection] literal): whitelist dura.
        raise HTTPException(status_code=422, detail=f"Collection not under versioning: {body.collection!r}.")
    try:
        res = await service.add_change(cs_id, user["username"], body.collection, body.entityId, body.op, body.payload,
                                       body.origin)
    except (CrossProjectError, ProjectDeletedError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DuplicateEntityError as exc:
        # Unicidad de nombres (spec 10 §9): el Save queda bloqueado ACÁ, en el
        # router — el publish queda protegido transitivamente (y re-chequeado).
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RelationshipKeyMismatchError as exc:
        # N=N (doc 47): la relación debe migrar la llave completa del padre.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except NameTooLongError as exc:
        # Nombre físico sobre el límite del naming config (Data Standards): 400
        # con el mensaje legible; el create popup lo muestra inline.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InvalidPayloadError as exc:
        # El upsert terminaría aplicado tal cual a la colección publicada:
        # payload que no valida contra el modelo NO entra al changeset.
        raise HTTPException(status_code=422, detail=f"Invalid change — {exc}") from exc
    if res is None:
        # Doc 104: la versión ya no existe (eliminada). Antes respondía 200 con
        # `null` — un «guardado» falso que el canvas daba por bueno.
        raise HTTPException(status_code=404, detail=GONE_DETAIL)
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


@router.put("/{cs_id}/changes/bulk")
async def add_changes_bulk(cs_id: str, body: ChangesBulkBody, user: dict = Depends(_can_edit)):
    """Lote de cambios en UNA request — cascada de borrar tabla / crear tabla
    desde fuentes (cambio-por-cambio era minutos a 4k columnas). Misma
    semántica y mapeo de errores que `/changes`; la validación corre completa
    ANTES de escribir, así un ítem inválido no deja la cascada a medias."""
    bad = sorted({c.collection for c in body.changes if c.collection not in VERSIONED})
    if bad:
        raise HTTPException(status_code=422, detail=f"Collection not under versioning: {', '.join(map(repr, bad))}.")
    try:
        res = await service.add_changes_bulk(
            cs_id, user["username"], [c.model_dump() for c in body.changes])
    except (CrossProjectError, ProjectDeletedError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except DuplicateEntityError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RelationshipKeyMismatchError as exc:
        # N=N (doc 47): la relación debe migrar la llave completa del padre.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except NameTooLongError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except InvalidPayloadError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid change — {exc}") from exc
    if res is None:
        raise HTTPException(status_code=404, detail=GONE_DETAIL)          # doc 104: eliminada
    if res == "forbidden":
        raise HTTPException(status_code=403, detail="Only the version owner can edit its working copy.")
    if res == "locked":
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
    tableIds: str | None = Query(default=None, description="sólo views: vistas de un LOTE de tablas (coma)"),
    principal: Principal = Depends(current_principal),
):
    """Estado efectivo de una colección. `tableId`/`ids` acotan la respuesta a
    un slice — obligatorio en colecciones grandes (canonical_columns).
    `q`+`limit`: búsqueda server-side por nombre (modales de catálogo).
    `schema`: tablas/vistas de UN esquema, draft-aware (Database Explorer)."""
    if collection not in VERSIONED:
        raise HTTPException(status_code=422, detail=f"Collection not under versioning: {collection!r}.")
    id_list = [s for s in (ids.split(",") if ids else []) if s] or None
    # Doc 105 (P2): vistas de un LOTE de tablas en una lectura («Import existing
    # tables» pedía una por tabla). Presente pero vacío o enorme → 422 (nunca
    # «todo el proyecto» por un lote mal armado).
    table_list: list[str] | None = None
    if tableIds is not None:
        if collection != "views":
            raise HTTPException(status_code=422, detail="tableIds only applies to views.")
        table_list = list(dict.fromkeys(s for s in tableIds.split(",") if s))
        if not table_list or len(table_list) > 300:
            raise HTTPException(status_code=422, detail="tableIds must list between 1 and 300 tables.")
    await ensure_changeset_visible(cs_id, principal)   # doc 70 §12
    return ok(await service.effective(cs_id, collection, table_id=tableId, ids=id_list,
                                      q=q, limit=limit, schema=schema, table_ids=table_list))


@router.get("/{cs_id}/diff")
async def diff(cs_id: str):
    """Diff estructurado por colección (added/edited/deleted con nombres) + impacto (p5)."""
    res = await service.diff(cs_id)
    if res is None:
        raise HTTPException(status_code=404, detail=GONE_DETAIL)          # doc 104: eliminada
    return ok(res)


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
    # Doc 105: un usuario DESHABILITADO tampoco vota nunca (misma traba).
    disabled = [r for r in body.reviewers if (users_by_id.get(r) or {}).get("status") == "disabled"]
    if disabled:
        names = ", ".join((users_by_id.get(d) or {}).get("name") or d for d in disabled)
        raise HTTPException(
            status_code=400,
            detail=f"Disabled user(s) can't review: {names}. Assign active reviewers.",
        )
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
    try:
        res = await service.submit(cs_id, user["username"], body.title, body.description,
                                   body.reviewers)
    except ProjectDeletedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if res == "forbidden":
        raise HTTPException(status_code=403, detail="Only the version owner can send it to review.")
    if res == "uploading":                                  # doc 105: carga Excel escribiendo
        raise HTTPException(status_code=409, detail=_ADMIN_ACTION_ERRORS["uploading"][1])
    if res == "incomplete-restore":                         # doc 105: restore a medias
        raise HTTPException(status_code=409, detail="This restore draft wasn't fully created: "
                                                    "delete it and restore the version again.")
    if res is None and not await service.exists(cs_id):
        raise HTTPException(status_code=404, detail=GONE_DETAIL)          # doc 104: eliminada
    if res is None:
        raise HTTPException(
            status_code=409,
            detail="The version is not in draft: it can't be sent to review "
                   "(if it's already in review, withdraw it first).",
        )
    await audit(user["username"], "changeset.submit", target=cs_id, target_type="changeset",
                meta={"reviewers": body.reviewers})
    return ok(res)


def _publish_detail(exc: PublishError) -> dict:
    """Doc 84 D1: `detail` estructurado de un publish bloqueado. `message` es lo
    que el cliente HTTP muestra (compat con `apiErrorMessage`); `items`/`next`
    alimentan el panel de bloqueo de la revisión."""
    d = exc.to_detail()
    d["next"] = d.get("next") or service.PUBLISH_NEXT_STEP
    return d


async def _decide(cs_id: str, actor: str, decision: str, note: str | None):
    """Registra la decisión de un revisor asignado vía `service.review` (política
    de UNANIMIDAD: sólo se aplica cuando TODOS los revisores aprobaron). Camino
    único de decisión — /review, /approve y /reject pasan por acá."""
    try:
        res = await service.review(cs_id, actor, decision, note)
    except service.RejectionReasonRequired as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc          # doc 88 §5
    except ProjectDeletedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except (CrossProjectError, DuplicateEntityError, SchemaInUseError, TableInUseError) as exc:
        # Doc 84 D1: detalle ESTRUCTURADO {code, message, items, next} — el front
        # pinta el panel «Publish blocked» con la lista completa. Duplicados =
        # carrera entre changesets (spec 10 §9: otro publish ganó el nombre);
        # esquema en uso (doc 18); referencia cruzada (doc 75 I2). En todos, el
        # claim ya se revirtió (producción intacta) y el request sigue en
        # revisión. Mismo 409, mismo guard, mismo momento: el bloqueo no cambia.
        raise HTTPException(status_code=409, detail=_publish_detail(exc)) from exc
    except InvalidPayloadError as exc:
        # Gate autoritativo del apply: el claim se revirtió, producción intacta.
        raise HTTPException(status_code=422, detail=_publish_detail(exc)) from exc
    if res == "forbidden":
        raise HTTPException(status_code=403, detail="You are not assigned as a reviewer of this request.")
    if res == service.SENT_BACK:
        # Doc 105 (ronda 3): la aprobación SÍ escribió producción (queda auditada),
        # pero en el medio la devolvieron a revisión: se re-aprueba y converge.
        await audit(actor, "changeset.decide", target=cs_id, target_type="changeset",
                    meta={"decision": decision, "result": service.SENT_BACK})
        raise HTTPException(
            status_code=409,
            detail=("This request was applied, but it was sent back to review while it was being applied: "
                    "approve it again to finish."),
        )
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
    try:
        res = await service.reopen(cs_id, user["username"])
    except ProjectDeletedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if res == "forbidden":
        raise HTTPException(status_code=403, detail="Only the version owner can reopen it.")
    if res is None and not await service.exists(cs_id):
        raise HTTPException(status_code=404, detail=GONE_DETAIL)          # doc 104: eliminada
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
    try:
        res = await service.rollback(cs_id, user["username"])
    except ProjectDeletedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    except (CrossProjectError, ProjectDeletedError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
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
    res = await service.add_comment(cs_id, principal.username, body.text)
    if res is None:
        raise HTTPException(status_code=404, detail=GONE_DETAIL)          # doc 104: eliminada
    return ok(res)


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
project_versions_router = APIRouter(prefix="/api/projects/{project_id}/versions", tags=["versions"],
                                    dependencies=[Depends(alive_project)])


@versions_router.get("")
async def list_versions(projectId: str | None = Query(default=None)):
    """Filas de versión para Home/Review: todas (con `projectId` por fila) o las
    de un proyecto (doc 75 D2)."""
    return ok(await service.list_versions(projectId))


@project_versions_router.get("")
async def project_versions(project_id: str):
    """Versiones DEL proyecto (doc 75 D2)."""
    return ok(await service.list_versions(project_id))


@project_versions_router.get("/published")
async def project_production(project_id: str):
    """Versión de producción actual DEL proyecto (la última approved/aplicada)."""
    return ok(await service.current_production(project_id))


def _compare_http(res):
    """Mapea los sentinels del compare (doc 65) a HTTP; devuelve el dict OK."""
    if res is None:
        raise HTTPException(status_code=404, detail="Version not found.")
    if res == "not-applied":
        raise HTTPException(status_code=409,
                            detail="Both versions must be published to compare them.")
    if res == "same":
        raise HTTPException(status_code=409,
                            detail="Pick two different versions to compare.")
    if res == "different-projects":
        raise HTTPException(status_code=409,
                            detail="Both versions must belong to the same project.")
    return res


@versions_router.get("/compare")
async def compare_versions(fromId: str = Query(...), toId: str = Query(...)):
    """Diferencias NETAS entre dos versiones publicadas (doc 65): buckets por
    colección + conteos + entidades irreconstruibles (pre before-images).
    READ-ONLY (mismo criterio de acceso que GET /api/versions y /diff);
    cualquier orden de extremos — se normaliza a cronológico."""
    return ok(_compare_http(await service.compare_versions(fromId, toId)))


@versions_router.post("/compare/details")
async def compare_details(body: CompareDetailsBody):
    """Diff de campos antes→después de entidades puntuales del rango comparado
    (doc 65) — shape del popup doc 31. Entidades fuera del rango se omiten."""
    bad = sorted({i.collection for i in body.items} - set(VERSIONED))
    if bad:
        raise HTTPException(status_code=400,
                            detail=f"Unknown collection(s): {', '.join(bad)}.")
    res = await service.compare_details(
        body.fromId, body.toId, [(i.collection, i.entityId) for i in body.items])
    return ok(_compare_http(res))


requests_router = APIRouter(prefix="/api/requests", tags=["requests"])


@requests_router.get("")
async def list_requests(
    reviewer: str | None = Query(default=None),
    owner: str | None = Query(default=None),
):
    """Publish requests en revisión. `reviewer` = asignado (p.ej. el actor);
    `owner` = solicitante. Sin filtros, todos los `submitted`."""
    return ok(await service.list_requests(reviewer, owner))
