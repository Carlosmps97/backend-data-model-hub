"""Negocio de `changesets`: registrar cambios, overlay, transiciones,
versiones/requests y diff enriquecido.

La política de aprobación vive en funciones **puras** (testables sin DB):
`next_version_label`, `record_approval`, `approval_outcome`, `is_assigned`,
`structured_diff`. Las funciones `async` sólo orquestan repository + estas puras.
"""
from __future__ import annotations

import uuid

import re
from datetime import datetime, timezone

from app.core.logging import get_logger
from app.core.naming import apply_case
from app.core.scope import PROJECT_SCOPED, ProjectDeletedError, scoped
from app.core.versioning import overlay, summarize_diff
from app.features.glossary import service as dict_svc
from app.features.projects import repository as projects_repo
from app.features.relationships.models import RelationshipDoc
from app.features.schemas import service as schemas_service
from app.features.settings import service as settings_service
from app.features.views.custom_sql import CustomSqlError, parse_custom_sql
from app.features.views.models import normalize_source_tables

from . import diffdetail, repository, validation
from . import asof
from .repository import VERSIONED
from .validation import (
    CrossProjectError, DuplicateEntityError, InvalidPayloadError, NameTooLongError,
    RelationshipKeyMismatchError, SchemaInUseError, collect_refs, cross_project_error,
    project_change_error, relationship_key_error)

log = get_logger("app.changesets")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Plan de aplicación ─────────────────────────────────────────────────────


def apply_plan(changes: dict) -> list[tuple]:
    """Linealiza `changes` en (collection, entityId, op, payload|None), en el
    ORDEN DE DEPENDENCIA de VERSIONED (dominios → diccionario → tablas →
    columnas → relaciones → vistas): el apply escribe una colección por vez sin
    transacción, y un fallo a mitad no debe poder dejar columnas publicadas de
    una tabla que nunca llegó a escribirse. Puro."""
    ordered = [c for c in VERSIONED if c in changes] + [c for c in changes if c not in VERSIONED]
    plan: list[tuple] = []
    for collection in ordered:
        for eid, ch in changes[collection].items():
            payload = ch.get("payload") if ch.get("op") != "delete" else None
            if collection == "views" and payload:
                # F3a: una vista publicada VÍA changeset debe quedar normalizada
                # igual que POST/PUT (tableId↔sourceTableIds) — el payload crudo
                # saltearía normalize_source_tables y un doc showOnCanvas=true
                # con solo `tableId` legacy sería invisible en todo canvas
                # (build_canvas_query matchea SOLO sourceTableIds). También
                # sanea drafts pendientes legacy.
                payload = normalize_source_tables(payload)
                # Doc 61: customColumns se RE-derivan del script al publicar
                # (el payload del draft pudo mentir); sin script → Regular.
                sql = (payload.get("customSql") or "").strip()
                if sql:
                    try:
                        payload = {**payload, "customSql": sql,
                                   "customColumns": parse_custom_sql(sql)["columns"]}
                    except CustomSqlError:
                        pass  # el gate validate_changes ya lo rechazó; defensivo
                else:
                    payload = {**payload, "customSql": None, "customColumns": []}
            plan.append((collection, eid, ch.get("op"), payload))
    return plan


def changes_in_cycle(changes: dict, submitted_at: str | None) -> dict:
    """Excluye cambios grabados DESPUÉS del envío (`at > submittedAt`). La
    compensación de `set_change` es post-escritura: una escritura tardía puede
    ser visible entre el claim y la lectura del apply — este filtro garantiza
    que lo aplicado es EXACTAMENTE lo que los revisores vieron. Cambios sin
    `at` (legacy) se conservan; ISO-8601 UTC compara lexicográficamente. Puro."""
    if not submitted_at:
        return changes
    out: dict = {}
    for col, col_changes in (changes or {}).items():
        kept = {eid: ch for eid, ch in col_changes.items()
                if not ch.get("at") or str(ch["at"]) <= str(submitted_at)}
        if kept:
            out[col] = kept
    return out


# ── Política de versionado / aprobación (pura) ────────────────────────────


def next_version_label(existing_labels) -> str:
    """Siguiente etiqueta autoincremental "vN" mirando las existentes.
    Ignora labels que no matcheen `v<entero>`; arranca en "v1"."""
    nums = []
    for lbl in existing_labels or []:
        m = re.fullmatch(r"v(\d+)", str(lbl or "").strip(), re.IGNORECASE)
        if m:
            nums.append(int(m.group(1)))
    return f"v{(max(nums) + 1) if nums else 1}"


class RejectionReasonRequired(ValueError):
    """Doc 88 §5: un rechazo SIN motivo no se registra (el router la convierte
    en 422). Buena práctica pedida por los modeladores: quien rechaza explica."""


def open_cycle(cs: dict, actor: str, submitted_at: str, title: str | None,
               description: str | None, reviewers: list[str] | None) -> list[dict]:
    """Doc 88 §6: historial + el ciclo `pending` de este envío. Puro."""
    history = [dict(r) for r in (cs.get("requests") or [])]
    history.append({
        "id": str(uuid.uuid4()), "cycle": len(history) + 1,
        "submittedAt": submitted_at, "submittedBy": actor,
        "title": title if title is not None else cs.get("title"),
        "description": description if description is not None else cs.get("description"),
        "reviewers": list(reviewers if reviewers is not None else (cs.get("reviewers") or [])),
        "outcome": "pending",
    })
    return history


def close_cycle(cs: dict, outcome: str, actor: str | None = None, note: str | None = None,
                decisions: dict | None = None) -> list[dict]:
    """Doc 88 §6: cierra el ciclo `pending` con `outcome` (approved | rejected |
    withdrawn). Un doc legacy sin historial (enviado antes del doc 88) gana un
    registro sintetizado desde su cabecera para no perder la solicitud. Puro."""
    history = [dict(r) for r in (cs.get("requests") or [])]
    if not history or history[-1].get("outcome") != "pending":
        history.append({
            "id": str(uuid.uuid4()), "cycle": len(history) + 1,
            "submittedAt": cs.get("submittedAt"), "submittedBy": cs.get("owner"),
            "title": cs.get("title"), "description": cs.get("description"),
            "reviewers": list(cs.get("reviewers") or []), "outcome": "pending",
        })
    last = history[-1]
    last.update({"outcome": outcome, "decidedAt": _now(), "decidedBy": actor})
    if note:
        last["note"] = note
    if decisions:
        last["decisions"] = {k: dict(v) for k, v in decisions.items()}
    return history


def is_assigned(reviewers, actor: str) -> bool:
    """¿El actor está asignado como revisor? (gate de 403 en review)."""
    return actor in (reviewers or [])


def record_approval(approvals: dict, actor: str, decision: str, note: str | None) -> dict:
    """Registra/actualiza la decisión de un revisor en `approvals[actor]`. Puro."""
    approvals = {k: dict(v) for k, v in (approvals or {}).items()}
    status = "approved" if decision == "approve" else "rejected"
    entry = {"status": status, "at": _now()}
    if note:
        entry["note"] = note
    approvals[actor] = entry
    return approvals


def approval_outcome(reviewers, approvals: dict) -> str:
    """Decide el estado del request a partir de las decisiones registradas:
    - algún revisor rechazó                       → 'rejected'
    - hay revisores y TODOS aprobaron             → 'approved'  (⇒ se aplica)
    - en otro caso                                → 'pending'   (sigue 'submitted')
    """
    reviewers = list(reviewers or [])
    approvals = approvals or {}
    if any(approvals.get(r, {}).get("status") == "rejected" for r in reviewers):
        return "rejected"
    if reviewers and all(approvals.get(r, {}).get("status") == "approved" for r in reviewers):
        return "approved"
    return "pending"


# ── Diff enriquecido + impacto (puro) ─────────────────────────────────────


def _entity_name(meta: dict, eid: str, payload: dict | None) -> str | None:
    """Nombre legible de una entidad: del payload del cambio o de la meta publicada."""
    src = payload if payload else meta.get(eid, {})
    return src.get("physicalName") or src.get("logicalName") or src.get("name")


def structured_diff(
    changes: dict,
    published: dict[str, list[dict]],
    relationships: list[dict] | None = None,
    baseline: str | None = None,
) -> dict:
    """Diff por colección con nombres + impacto + conflictos. Puro.

    - `changes`: dict completo del changeset {collection: {eid: {op, payload?, at?}}}.
    - `published`: {collection: [doc,...]} estado publicado de cada colección.
    - `relationships`: relaciones publicadas (para afectación a otras tablas).
    - `baseline`: fallback (createdAt del changeset) cuando el cambio no trae `at`.

    Devuelve `{collections: {col: {added,edited,deleted}}, impact: {...}}` donde
    cada item es `{id, name, collection, conflict}`. `conflict=True` cuando la
    entidad fue modificada en PRODUCCIÓN después de que este changeset la editó
    (publicado.updatedAt > at del cambio) — al publicar, este delta la pisaría.
    El impacto cuenta tablas/columnas tocadas y las OTRAS tablas afectadas vía
    relationships con las tocadas.
    """
    out_cols: dict[str, dict] = {}
    tables_touched: set[str] = set()
    columns_touched: set[str] = set()

    for col, col_changes in (changes or {}).items():
        meta = {d["id"]: d for d in published.get(col, [])}
        sd = summarize_diff(published.get(col, []), col_changes)
        buckets = {"added": [], "edited": [], "deleted": []}
        bucket_of = {"added": "added", "modified": "edited", "removed": "deleted"}
        for kind, eids in sd.items():
            for eid in eids:
                ch = col_changes.get(eid) or {}
                payload = ch.get("payload")
                # ISO-8601 UTC compara bien lexicográficamente. Sin timestamps
                # (docs viejos sin `at` ni baseline, o publicado sin updatedAt)
                # no se puede juzgar → sin flag (conservador).
                edited_at = ch.get("at") or baseline
                pub_updated = (meta.get(eid) or {}).get("updatedAt")
                conflict = bool(edited_at and pub_updated and str(pub_updated) > str(edited_at))
                buckets[bucket_of[kind]].append(
                    {"id": eid, "name": _entity_name(meta, eid, payload), "collection": col, "conflict": conflict}
                )
        out_cols[col] = buckets

        # Impacto: qué tablas/columnas toca este changeset.
        if col == "canonical_tables":
            tables_touched.update(col_changes.keys())
        elif col == "canonical_columns":
            for eid, ch in col_changes.items():
                columns_touched.add(eid)
                tid = ((ch.get("payload") or {}).get("tableId")) or meta.get(eid, {}).get("tableId")
                if tid:
                    tables_touched.add(tid)

    affected = _affected_other_tables(tables_touched, relationships or [], published)

    return {
        "collections": out_cols,
        "impact": {
            "tablesTouched": len(tables_touched),
            "columnsTouched": len(columns_touched),
            "affectedOtherTables": affected,
        },
    }


def _affected_other_tables(
    touched_table_ids: set[str], relationships: list[dict], published: dict[str, list[dict]]
) -> list[dict]:
    """Otras tablas conectadas por relationships a las tocadas (excluidas ellas).
    Devuelve `[{id, name}]`."""
    names = {d["id"]: (d.get("physicalName") or d.get("logicalName") or d.get("name"))
             for d in published.get("canonical_tables", [])}
    affected: set[str] = set()
    for rel in relationships:
        parent = rel.get("parentTableId") or rel.get("targetTableId")
        child = rel.get("childTableId") or rel.get("sourceTableId")
        if parent in touched_table_ids and child and child not in touched_table_ids:
            affected.add(child)
        if child in touched_table_ids and parent and parent not in touched_table_ids:
            affected.add(parent)
    return [{"id": tid, "name": names.get(tid)} for tid in sorted(affected)]


def version_row(cs: dict, deletes_project: bool = False) -> dict:
    """Proyección de un changeset como fila de la tabla de versiones (p5).
    Doc 75: `projectId` siempre; `deletesProject` marca el draft/request que
    borra su proyecto (pill crítica en la bandeja, D20)."""
    return {
        "id": cs["id"],
        "versionLabel": cs.get("versionLabel"),
        "title": cs.get("title"),
        "owner": cs.get("owner"),
        "status": cs.get("status"),
        "projectId": cs.get("projectId"),
        "deletesProject": deletes_project,
        "reviewers": cs.get("reviewers", []),
        "createdAt": cs.get("createdAt"),
        "updatedAt": cs.get("updatedAt"),
        "submittedAt": cs.get("submittedAt"),
        "appliedAt": cs.get("appliedAt"),
        "restoredFrom": cs.get("restoredFrom"),
        # Doc 88 §6: historial de solicitudes + último ciclo (badge de Review).
        "requests": list(cs.get("requests") or []),
        "lastRequest": (cs.get("requests") or [None])[-1],
        "reviewedBy": cs.get("reviewedBy"),
        "reviewedAt": cs.get("reviewedAt"),
        "reviewNote": cs.get("reviewNote"),
    }


def history_events(changes: list[dict], headers: dict[str, dict]) -> list[dict]:
    """Eventos de auditoría de UNA entidad (doc 51) a partir de sus cambios con
    imagen estampada + cabeceras de changeset. Solo cuentan changesets
    APLICADOS (`approved` con `appliedAt` — mismo criterio que
    current_production): un draft/snapshot que nunca publicó no genera
    historial. Clasificación por cambio: delete → deleted; upsert sin `before`
    (no existía publicada) → created; upsert con `before` → edited. `origin`
    solo aflora en un created (el carry-forward del draft lo deja también en
    ediciones colapsadas, donde mostrarlo mentiría). Más reciente primero. Puro."""
    events: list[dict] = []
    for ch in changes:
        hdr = headers.get(str(ch.get("csId") or ""))
        if not hdr or hdr.get("status") != "approved" or not hdr.get("appliedAt"):
            continue
        op = ch.get("op")
        action = "deleted" if op == "delete" else (
            "created" if ch.get("before") is None else "edited")
        approved = sorted(uid for uid, e in (hdr.get("approvals") or {}).items()
                          if (e or {}).get("status") == "approved")
        events.append({
            "action": action,
            "at": hdr.get("appliedAt"),
            "editedAt": ch.get("at"),
            "csId": hdr.get("id"),
            "version": hdr.get("versionLabel"),
            "versionTitle": hdr.get("title"),
            "userId": hdr.get("owner"),
            "publishedById": hdr.get("reviewedBy"),
            "approvedByIds": approved,
            "restoredFrom": hdr.get("restoredFrom"),
            "origin": ch.get("origin") if action == "created" else None,
            # Insumo del detalle de campos (entity_detail): before estampado.
            "change": {"op": op, "payload": ch.get("payload"), "at": ch.get("at"),
                       "before": ch.get("before"), "beforeAt": ch.get("beforeAt")},
        })
    events.sort(key=lambda e: e.get("at") or "", reverse=True)
    return events


# ── Orquestación async (repository + puras) ───────────────────────────────


async def ensure_project_alive(cs: dict) -> None:
    """Doc 75 I11: nada se edita, envía, publica ni restaura sobre un proyecto borrado."""
    if not await projects_repo.get_project(cs["projectId"]):
        raise ProjectDeletedError("This project was deleted.")


async def applied_versions(project_id: str) -> list[dict]:
    return [cs for cs in await repository.list_summaries(project_id)
            if cs.get("status") == "approved" and cs.get("appliedAt")]


async def create_base_marker(project_id: str, title: str = "Initial version", owner: str = "system") -> dict:
    """Marcador `v1` (approved, 0 cambios) para que Open model tenga producción desde
    el primer día (doc 75 D5). Idempotente: si el proyecto ya tiene versiones
    aplicadas devuelve la más vieja."""
    applied = await applied_versions(project_id)
    if applied:
        return min(applied, key=lambda c: c.get("appliedAt") or "")
    now = _now()
    return await repository.create(title, owner, extra={
        "projectId": project_id, "status": "approved", "versionLabel": "v1",
        "description": "Base version of the project (no changes).",
        "submittedAt": now, "reviewedBy": owner, "reviewedAt": now, "appliedAt": now,
        "reviewNote": "Marker created by the platform."})


async def get(cs_id: str) -> dict | None:
    version_id = asof.asof_version_id(cs_id)
    if version_id is not None:
        # Doc 70: cabecera de la versión objetivo bajo el id virtual (solo
        # lectura; sin diff — el snapshot no es un request).
        target = await repository.get(version_id)
        if not target:
            return None
        return {**target, "id": cs_id, "diff": {},
                "asOf": {"versionId": version_id, "versionLabel": target.get("versionLabel"),
                         "appliedAt": target.get("appliedAt")}}
    cs = await repository.get(cs_id)
    if not cs:
        return None
    # Adjunta un resumen de diff por colección para el aprobador (compat M-series).
    # Slice por ids cambiados: summarize_diff sólo clasifica los ids del changeset,
    # no necesita (ni debe cargar) la colección publicada completa.
    changes = await repository.changes_map(cs_id)
    diff = {}
    for col in VERSIONED:
        col_changes = changes.get(col)
        if col_changes:
            pub = await repository.published(col, {"_id": {"$in": list(col_changes.keys())}})
            diff[col] = summarize_diff(pub, col_changes)
    return {**cs, "diff": diff}


async def list_all() -> list[dict]:
    return await repository.list_all()


async def _rows(summaries: list[dict]) -> list[dict]:
    deleting = await repository.changesets_deleting_project([cs["id"] for cs in summaries])
    return [version_row(cs, deletes_project=cs["id"] in deleting) for cs in summaries]


async def list_versions(project_id: str | None = None) -> list[dict]:
    """Filas de versión (sin el `changes` crudo): todas (Home, con `projectId`
    por fila) o sólo las de un proyecto (doc 75 D2)."""
    return await _rows(await repository.list_summaries(project_id))


async def current_production(project_id: str) -> dict | None:
    """Última versión APLICADA DEL PROYECTO (fila de producción verde en el UI).
    Prefiere las approved con `appliedAt` (apply completo confirmado): una
    `approved` sin appliedAt es un publish interrumpido a mitad — no está en
    producción. Fallback a approved a secas para docs legacy previos al marcador."""
    approved = [cs for cs in await repository.list_summaries(project_id) if cs.get("status") == "approved"]
    if not approved:
        return None
    applied = [cs for cs in approved if cs.get("appliedAt")]
    pool = applied or approved
    pool.sort(key=lambda c: c.get("appliedAt") or c.get("reviewedAt") or c.get("updatedAt") or "", reverse=True)
    return version_row(pool[0])


async def list_requests(reviewer: str | None = None, owner: str | None = None) -> list[dict]:
    """Publish requests en revisión (`submitted`). Filtra por revisor asignado
    (`reviewer`, p.ej. el actor) y/o por solicitante (`owner`)."""
    out = []
    for cs in await repository.list_summaries():
        if cs.get("status") != "submitted":
            continue
        if reviewer is not None and reviewer not in cs.get("reviewers", []):
            continue
        if owner is not None and cs.get("owner") != owner:
            continue
        out.append(cs)
    return await _rows(out)


async def snapshot(actor: str, project_id: str, title: str | None, description: str | None,
                   version_label: str | None) -> dict:
    """Crea un draft (working copy) DEL PROYECTO a partir de su estado publicado.
    `versionLabel` autoincremental POR PROYECTO si no se pasa; `owner = actor`."""
    if not await projects_repo.get_project(project_id):
        raise ProjectDeletedError("This project was deleted.")
    if not version_label:
        existing = [cs.get("versionLabel") for cs in await repository.list_summaries(project_id)]
        version_label = next_version_label(existing)
    fields = {
        "description": description,
        "versionLabel": version_label,
        "projectId": project_id,
    }
    return await repository.create(title or version_label, actor, extra=fields)


def _table_name_filter(payload: dict) -> dict:
    """Parte por NOMBRE del chequeo de unicidad de tabla (doc 50): physicalName
    por regex anclado case-insensitive — el esquema no participa en la clave
    (A.M_CLIENTE y B.M_CLIENTE no pueden coexistir DENTRO del proyecto). Puro."""
    name = str(payload.get("physicalName") or "").strip()
    return {"physicalName": {"$regex": f"^{re.escape(name)}$", "$options": "i"}}


def _table_dup_filter(project_id: str, payload: dict) -> dict:
    """Filtro Mongo del chequeo de unicidad de tabla: POR PROYECTO (doc 75 D6)
    + nombre. Puro."""
    return scoped(project_id, _table_name_filter(payload))


async def _project_of_changeset(cs_id: str) -> str | None:
    """Proyecto de un changeset (también `asof:<versionId>`)."""
    version_id = asof.asof_version_id(cs_id)
    doc = await repository.get(version_id or cs_id)
    return doc.get("projectId") if doc else None


def _stamp_project(cs: dict, collection: str, op: str, payload: dict | None) -> dict | None:
    """Doc 75 I1: el proyecto del payload es SIEMPRE el del changeset (se pisa
    lo que traiga el cliente)."""
    if op == "delete" or payload is None or collection not in PROJECT_SCOPED:
        return payload
    return {**payload, "projectId": cs["projectId"]}


async def _resolve_owners(cs: dict, refs: dict[str, set[str]], pending: dict[str, dict]) -> dict:
    """projectId efectivo de cada referencia: pendiente del MISMO changeset
    (upsert ⇒ del proyecto; delete ⇒ inexistente) o publicado."""
    owners: dict[str, dict[str, str | None]] = {}
    for coll, ids in refs.items():
        per: dict[str, str | None] = {}
        missing: list[str] = []
        for i in ids:
            ch = (pending.get(coll) or {}).get(i)
            if ch is not None:
                per[i] = None if ch.get("op") == "delete" else cs["projectId"]
            else:
                missing.append(i)
        if missing:
            docs = await repository.published(coll, {"_id": {"$in": missing}}, projection={"projectId": 1})
            found = {d["id"]: d.get("projectId") for d in docs}
            for i in missing:
                per[i] = found.get(i)
        owners[coll] = per
    return owners


# Doc 88 §4: campos de MEMBRESÍA de un canvas → colección referida.
_MEMBERSHIP_FIELDS = {"tableIds": "canonical_tables", "viewIds": "views"}
_MEMBERSHIP_COLLS = frozenset(_MEMBERSHIP_FIELDS.values())


def prune_deleted_members(payload: dict | None, pending: dict[str, dict]) -> dict | None:
    """Doc 88 §4: un payload de canvas descarta de `tableIds` / `viewIds` (y de
    `layout`) los ids cuyo cambio pendiente EN ESTE changeset es un delete.
    Borrar una tabla o una vista es trabajo normal del modelador: su membresía
    residual en los canvases no puede convertirse en «View X doesn't exist in
    this project» al mover una tabla o al publicar. Un id que no existe o es de
    otro proyecto sigue fallando en el gate. `viewIds` None (canvas legacy,
    doc 70) se conserva. Puro."""
    if not payload:
        return payload
    gone: set[str] = set()
    for field, coll in _MEMBERSHIP_FIELDS.items():
        ids = payload.get(field)
        if not isinstance(ids, list):
            continue
        per = pending.get(coll) or {}
        gone |= {str(i) for i in ids if (per.get(str(i)) or {}).get("op") == "delete"}
    if not gone:
        return payload
    out = dict(payload)
    for field in _MEMBERSHIP_FIELDS:
        if isinstance(out.get(field), list):
            out[field] = [i for i in out[field] if str(i) not in gone]
    if isinstance(out.get("layout"), dict):
        out["layout"] = {k: v for k, v in out["layout"].items() if k not in gone}
    return out


async def _pruned_payload(cs: dict, collection: str, op: str, payload: dict | None,
                          pending: dict[str, dict] | None = None) -> dict | None:
    """Doc 88 §4: payload a persistir — un upsert de canvas vuelve PODADO de los
    miembros que este changeset borra (ver `prune_deleted_members`); el resto
    pasa tal cual. Corre ANTES del gate de referencias."""
    if collection != "subject_areas" or op == "delete" or not payload:
        return payload
    if pending is None:
        pending = await repository.changes_map(cs["id"], sorted(_MEMBERSHIP_COLLS))
    return prune_deleted_members(payload, pending)


async def _cross_project_check(cs: dict, collection: str, entity_id: str, op: str, payload: dict | None,
                               pending: dict[str, dict] | None = None) -> None:
    """Doc 75 I2/D7: toda referencia de un cambio se resuelve DENTRO del proyecto
    del changeset; `projects` sólo admite cambios sobre el propio proyecto (D5)."""
    if collection == "projects":
        err = project_change_error(cs["projectId"], entity_id, op, payload)
        if err:
            raise CrossProjectError(err)
        return
    refs = collect_refs(collection, payload)
    if not refs:
        return
    if pending is None:
        pending = await repository.changes_map(cs["id"], sorted(refs))
    err = cross_project_error(cs["projectId"], refs, await _resolve_owners(cs, refs, pending))
    if err:
        raise CrossProjectError(err)


def _without_members(payload: dict, ids: list[str]) -> dict:
    """Canvas sin esos miembros (membresía + layout). Puro."""
    gone = set(ids)
    out = dict(payload)
    for field in _MEMBERSHIP_FIELDS:
        if isinstance(out.get(field), list):
            out[field] = [i for i in out[field] if i not in gone]
    if isinstance(out.get("layout"), dict):
        out["layout"] = {k: v for k, v in out["layout"].items() if k not in gone}
    return out


async def _membership_cascade(cs: dict, collection: str, entity_id: str,
                              pending_sas: dict[str, dict] | None = None) -> list[dict]:
    """Doc 88 §4: al BORRAR una tabla o una vista, los canvases del proyecto que
    la listan — publicados y/o con upsert pendiente en este changeset — reciben
    un upsert de su doc EFECTIVO sin el id (membresía + `layout`). Devuelve los
    ítems `{collection, entityId, op, payload}` a grabar en el MISMO request,
    antes del delete. El borrado de tabla ya lo cascadeaba el front canvas por
    canvas; el de vista no tenía cascada alguna."""
    field = next((f for f, c in _MEMBERSHIP_FIELDS.items() if c == collection), None)
    if field is None:
        return []
    if pending_sas is None:
        pending_sas = (await repository.changes_map(cs["id"], ["subject_areas"])).get("subject_areas", {})
    effective: dict[str, dict] = {
        str(d["id"]): d for d in await repository.published("subject_areas", scoped(cs["projectId"], {field: entity_id}))
    }
    for sid, ch in pending_sas.items():
        # El pendiente PISA al publicado (overlay): si ya no la lista, no hay nada que sanear.
        effective.pop(sid, None)
        if ch.get("op") != "delete" and entity_id in ((ch.get("payload") or {}).get(field) or []):
            effective[sid] = {**(ch.get("payload") or {}), "id": sid}
    items: list[dict] = []
    for sid, doc in effective.items():
        if entity_id not in (doc.get(field) or []):
            continue
        payload = {k: v for k, v in doc.items() if k not in ("flgactive", "deletedAt")}
        payload[field] = [i for i in payload[field] if i != entity_id]
        if isinstance(payload.get("layout"), dict):
            payload["layout"] = {k: v for k, v in payload["layout"].items() if k != entity_id}
        items.append({"collection": "subject_areas", "entityId": sid, "op": "upsert", "payload": payload})
    return items


async def _table_name_grandfathered(project_id: str, entity_id: str, payload: dict | None) -> bool:
    """Grandfather de la unicidad de tablas POR PROYECTO (doc 50, mismo patrón que el
    de maxLength): si el upsert NO cambia el físico respecto del PUBLICADO de
    la MISMA entidad (CI), el duplicado no bloquea — la data legacy con
    homónimos entre esquemas (migrada bajo la regla vieja schema+nombre) sigue
    siendo editable. Crear una tabla nueva o RENOMBRAR hacia un nombre tomado
    sí bloquea. Se invoca SOLO cuando ya se detectó conflicto (el camino feliz
    no paga la query por _id)."""
    name = str((payload or {}).get("physicalName") or "").strip().lower()
    if not name:
        return False
    pub = await repository.published("canonical_tables", scoped(project_id, {"_id": entity_id}))
    # Guard por id además del filtro: el doc comparado debe ser LA MISMA entidad.
    doc = next((d for d in pub if str(d.get("id") or "") == entity_id), None)
    return doc is not None and str(doc.get("physicalName") or "").strip().lower() == name


async def _duplicate_error(cs: dict, collection: str, entity_id: str,
                           op: str, payload: dict | None) -> str | None:
    """Chequeo de unicidad de un upsert (spec 10 §9 + doc 18) contra el
    publicado ACTIVO DEL PROYECTO (slice indexado: tablas por project_id+
    physicalName, columnas por tableId, esquemas por name) + los upserts
    PENDIENTES del mismo changeset. None si pasa o no aplica."""
    if op == "delete" or collection not in ("canonical_tables", "canonical_columns", "schemas"):
        return None
    pid = cs["projectId"]
    p = payload or {}
    if collection == "schemas":
        name = str(p.get("name") or "").strip()
        if not name:
            return None
        pub = await repository.published(
            collection, scoped(pid, {"name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}}))
    elif not str(p.get("physicalName") or "").strip():
        return None
    elif collection == "canonical_tables":
        pub = await repository.published(collection, _table_dup_filter(pid, p))
    else:
        if not p.get("tableId"):
            return None
        pub = await repository.published(collection, {"tableId": p["tableId"]})
    pending = (await repository.changes_map(cs["id"], [collection])).get(collection, {})
    dup = validation.duplicate_error(collection, entity_id, p, pub, pending)
    if dup and collection == "canonical_tables" and await _table_name_grandfathered(pid, entity_id, p):
        return None
    return dup


async def _name_length_error(cs: dict, collection: str, entity_id: str,
                             op: str, payload: dict | None) -> str | None:
    """Límite de caracteres del nombre FÍSICO (naming config / Data Standards ·
    Glosario). Solo tablas/columnas en upsert. Se dispara al CREAR o RENOMBRAR;
    un nombre largo HEREDADO que NO cambia no se penaliza (grandfather). None si
    pasa o no aplica. El chequeo de longitud corta ANTES de tocar la BD (caso
    común); solo si excede busca el nombre actual."""
    if op == "delete" or collection not in ("canonical_tables", "canonical_columns"):
        return None
    name = str((payload or {}).get("physicalName") or "").strip()
    if not name:
        return None
    scope = "table" if collection == "canonical_tables" else "column"
    max_len = int((await settings_service.get_naming_for(cs["projectId"], scope)).get("maxLength") or 0)
    if not max_len or len(name) <= max_len:
        return None  # dentro del límite (o límite en 0 = desactivado)
    # Excede: bloquear SOLO si es nuevo o el físico CAMBIÓ respecto del estado
    # efectivo (pendiente del changeset o publicado).
    pend = (await repository.changes_map(cs["id"], [collection])).get(collection, {})
    current: str | None = None
    if entity_id in pend:
        current = str((pend[entity_id].get("payload") or {}).get("physicalName") or "").strip() or None
    if current is None:
        pub = await repository.published(collection, {"_id": entity_id})
        if pub:
            current = str(pub[0].get("physicalName") or "").strip() or None
    if current is not None and current.casefold() == name.casefold():
        return None  # heredado sin cambios (doc 83: el case no cuenta como rename)
    return _too_long_msg(scope, name, max_len)


def _too_long_msg(scope: str, name: str, max_len: int) -> str:
    """Mensaje único del límite de longitud (single y lote). Puro."""
    return (f"Can't save this {scope}: the physical name «{name}» has {len(name)} "
            f"characters, over the {max_len}-character limit. Shorten the logical "
            f"name, or raise the limit in Data Standards · Glossary.")


async def _relationship_key_error(cs_id: str, payload: dict,
                                  bulk_cols: dict[str, dict] | None = None) -> str | None:
    """Guard N=N (doc 47): el set efectivo de PKs del padre = columnas
    publicadas + overlay pendiente del changeset (+ los ítems de columnas del
    MISMO lote, si vienen). Sólo se dispara al ESCRIBIR relaciones — editar la
    llave del padre después no bloquea (el drift lo resuelve el Sync del front)."""
    parent_tid = payload.get("parentTableId")
    if not parent_tid:
        return None  # payload_error ya lo exigió antes
    pub = await repository.published("canonical_columns", {"tableId": parent_tid})
    pending = (await repository.changes_map(cs_id, ["canonical_columns"])).get("canonical_columns", {})
    if bulk_cols:
        pending = {**pending, **bulk_cols}
    ids_pub = {c["id"] for c in pub}

    def _touches(ch: dict) -> bool:
        return (ch.get("payload") or {}).get("tableId") == parent_tid

    cols = overlay(pub, {eid: ch for eid, ch in pending.items()
                         if eid in ids_pub or _touches(ch)})
    pk_ids = {c["id"] for c in cols
              if c.get("tableId") == parent_tid and c.get("isPrimaryKey")}
    return relationship_key_error(payload, pk_ids)


async def add_change(cs_id: str, actor: str, collection: str, entity_id: str, op: str, payload: dict | None,
                     origin: dict | None = None) -> dict | str | None:
    """Graba un cambio como documento propio (`changeset_changes`), sólo si el
    changeset sigue en `draft` (protocolo con compensación en el repository).

    Valida el payload contra el modelo de la colección ANTES de escribir
    (levanta `InvalidPayloadError` → 422): un upsert malformado terminaría
    aplicado tal cual a la colección publicada en el publish.

    Devuelve "forbidden" si el actor NO es el owner: la working copy es
    personal, y submit/withdraw/reopen ya son owner-only — este es el backstop
    de la escritura que esos guards protegen. Devuelve "locked" si el changeset
    existe pero YA NO está en draft (fue enviado a revisión / cerrado): el
    router los convierte en 403/409 para que el cliente NO falle en silencio."""
    cs = await repository.get(cs_id)
    if not cs:
        return None
    if cs.get("owner") != actor:
        return "forbidden"
    await ensure_project_alive(cs)
    payload = _stamp_project(cs, collection, op, payload)          # doc 75 I1
    err = validation.payload_error(collection, entity_id, op, payload)
    if err:
        raise InvalidPayloadError(err)
    if collection == "relationships" and op == "upsert" and payload:
        # Se persiste el dump v2 NORMALIZADO (parent/child + pairs): el overlay
        # y el apply sirven/escriben el payload tal cual se grabó, así que un
        # shape legacy (source/target) no debe quedar grabado en el draft.
        payload = RelationshipDoc.model_validate({**payload, "id": entity_id}).model_dump()
        # N=N (doc 47): la relación migra la llave COMPLETA del padre.
        key_err = await _relationship_key_error(cs_id, payload)
        if key_err:
            raise RelationshipKeyMismatchError(key_err)
    # Doc 88 §4: un canvas se graba podado de los miembros que este draft borró.
    payload = await _pruned_payload(cs, collection, op, payload)
    # Doc 75 I2: toda referencia se resuelve dentro del proyecto del changeset.
    await _cross_project_check(cs, collection, entity_id, op, payload)
    # Doc 88 §4: borrar una tabla/vista saca su membresía de los canvases que la
    # listan, en el MISMO request y antes del delete (cascada server-side).
    if op == "delete" and collection in _MEMBERSHIP_COLLS:
        for it in await _membership_cascade(cs, collection, entity_id):
            sa_payload = _stamp_project(cs, "subject_areas", "upsert", it["payload"])
            await _cross_project_check(cs, "subject_areas", it["entityId"], "upsert", sa_payload)
            if await repository.set_change(cs_id, "subject_areas", it["entityId"], "upsert", sa_payload) is None:
                return "locked" if await repository.get(cs_id) else None
    # Doc 83: reglas de naming (case del físico de columnas + override, doc 68)
    # ANTES de unicidad/longitud: esos chequeos ven el nombre que se persiste.
    await _apply_naming_rules(cs["projectId"], [{"collection": collection, "op": op, "payload": payload}])
    # Unicidad de nombres (spec 10 §9) POR PROYECTO: tablas por physicalName y
    # columnas por physicalName dentro de su tableId — contra publicado activo
    # + pendientes de ESTE changeset. El router lo convierte en 409.
    dup = await _duplicate_error(cs, collection, entity_id, op, payload)
    if dup:
        raise DuplicateEntityError(dup)
    too_long = await _name_length_error(cs, collection, entity_id, op, payload)
    if too_long:
        raise NameTooLongError(too_long)
    updated = await repository.set_change(cs_id, collection, entity_id, op, payload, origin)
    if updated is not None:
        return updated
    return "locked" if await repository.get(cs_id) else None


# Colecciones con chequeo de unicidad de nombres (spec 10 §9 + doc 18).
_UNIQUE_COLLS = ("canonical_tables", "canonical_columns", "schemas")

# Doc 68: colecciones cuyo `physicalName` se deriva del lógico por scope.
_OVERRIDE_SCOPES = {"canonical_tables": "table", "canonical_columns": "column"}
# Doc 83: colecciones cuyo físico TIPEADO se normaliza al `case` del scope.
# Sólo columnas (alcance del pedido owner 2026-09-10); las tablas siguen
# entrando tal cual — extender = sumar la colección acá.
_CASE_NORMALIZED = ("canonical_columns",)


async def _apply_naming_rules(project_id: str, items: list[dict]) -> None:
    """Aplica las reglas de naming del scope a upserts de tablas/columnas, en
    este orden y con UNA carga de reglas por scope presente en el lote:

    1. Doc 83 — `physicalName` de COLUMNAS al `case` del scope (`apply_case`):
       la regla sólo regía al derivar desde el lógico; tipear el físico (popup
       New column en modo Physical, panel Properties) dejaba pasar minúsculas.
       Este choke point cubre TODOS los escritores (panels, popup, CTAS, paste
       `_copy_n`, subcategorías `_DUPn`, bulk upload) sin tocar cada feature.
    2. Doc 68 — estampa `physicalNameOverridden`: flag del payload OR (físico
       ≠ physicalize(lógico) con las reglas vigentes). Sin esto, un físico
       custom quedaría re-derivable y el próximo Save & apply del Glosario lo
       pisaría. Se evalúa sobre el físico YA normalizado.

    Va ANTES de los chequeos de unicidad/longitud: esos ven el nombre que se
    persiste. Si las reglas no cargan (naming inaccesible / unit tests con
    repos mockeados), el payload pasa tal cual: JAMÁS bloquea la escritura."""
    rules: dict[str, tuple | None] = {}
    for it in items:
        scope = _OVERRIDE_SCOPES.get(it.get("collection") or "")
        if not scope or it.get("op") != "upsert" or not it.get("payload"):
            continue
        if scope not in rules:
            try:
                rules[scope] = await dict_svc.naming_rules(project_id, scope)
            except Exception:
                log.warning("naming rules unavailable — physical case/override NOT applied",
                            extra={"scope": scope})
                rules[scope] = None
        loaded = rules[scope]
        if loaded is None:
            continue
        mappings, sep, case = loaded
        payload = it["payload"]
        if it["collection"] in _CASE_NORMALIZED and isinstance(payload.get("physicalName"), str):
            payload["physicalName"] = apply_case(payload["physicalName"], case)
        payload["physicalNameOverridden"] = dict_svc.is_physical_override(
            payload, mappings, sep, case)


async def add_changes_bulk(cs_id: str, actor: str, items: list[dict]) -> dict | str | None:
    """Graba un LOTE de cambios de una vez — misma semántica que `add_change`
    repetido en orden. Las cascadas (borrar tabla: vistas + relaciones +
    columnas; crear tabla desde fuentes: tabla + columnas) iban cambio por
    cambio: N requests secuenciales = minutos a 4k columnas.

    Diferencias deliberadas con el loop: la validación corre COMPLETA antes de
    escribir (un ítem inválido no deja la cascada a medias) y los slices de
    unicidad se consultan UNA vez por lote — un lote de puros deletes no paga
    ninguna query extra. La unicidad se evalúa contra un pending que EVOLUCIONA
    ítem a ítem (un delete del lote libera su nombre para un upsert posterior,
    igual que en secuencia). Devuelve "forbidden"/"locked"/None como add_change."""
    cs = await repository.get(cs_id)
    if not cs:
        return None
    if cs.get("owner") != actor:
        return "forbidden"
    await ensure_project_alive(cs)
    # Dedupe por entidad (último gana): un cambio por (collection, entityId),
    # como quedaría tras el loop secuencial (el doc de cambio es único por key).
    deduped: dict[tuple[str, str], dict] = {}
    for it in items:
        deduped[(it["collection"], it["entityId"])] = dict(it)
    ordered = list(deduped.values())
    if not ordered:
        return cs
    # Doc 88 §4: los deletes de tablas/vistas del lote cascadean su membresía a
    # los canvases que NO vienen en el lote (los del lote se podan en el gate).
    gone = [it for it in ordered if it["op"] == "delete" and it["collection"] in _MEMBERSHIP_COLLS]
    if gone:
        pending_sas = (await repository.changes_map(cs_id, ["subject_areas"])).get("subject_areas", {})
        cascade: dict[str, dict] = {}
        for it in gone:
            for extra in await _membership_cascade(cs, it["collection"], it["entityId"], pending_sas):
                if ("subject_areas", extra["entityId"]) in deduped:
                    continue
                prev = cascade.get(extra["entityId"])
                if prev is not None:   # dos borrados sobre el mismo canvas: se acumulan
                    extra["payload"] = _without_members(prev["payload"], [i["entityId"] for i in gone])
                cascade[extra["entityId"]] = extra
        ordered = list(cascade.values()) + ordered
    for it in ordered:                                            # doc 75 I1
        it["payload"] = _stamp_project(cs, it["collection"], it["op"], it.get("payload"))

    errors = [e for it in ordered
              if (e := validation.payload_error(it["collection"], it["entityId"], it["op"], it.get("payload")))]
    if errors:
        raise InvalidPayloadError(" | ".join(errors[:5]))
    for it in ordered:
        if it["collection"] == "relationships" and it["op"] == "upsert" and it.get("payload"):
            # Igual que add_change: se persiste el dump v2 NORMALIZADO.
            it["payload"] = RelationshipDoc.model_validate(
                {**it["payload"], "id": it["entityId"]}).model_dump()

    # N=N (doc 47): las relaciones del lote validan contra la llave efectiva
    # del padre INCLUYENDO las columnas del propio lote (p.ej. la migración de
    # llave crea las columnas hijas en el mismo request).
    bulk_cols = {it["entityId"]: {"op": it["op"], "payload": it.get("payload") or {}}
                 for it in ordered if it["collection"] == "canonical_columns"}
    for it in ordered:
        if it["collection"] == "relationships" and it["op"] == "upsert" and it.get("payload"):
            key_err = await _relationship_key_error(cs_id, it["payload"], bulk_cols)
            if key_err:
                raise RelationshipKeyMismatchError(key_err)

    # Doc 75 I2: referencias del lote resueltas contra pendientes del changeset
    # + los ítems del PROPIO lote (una tabla y sus columnas en la misma tanda).
    ref_colls = sorted({c for it in ordered for c in collect_refs(it["collection"], it.get("payload"))}
                       | {"projects"} if any(it["collection"] == "projects" for it in ordered) else
                       {c for it in ordered for c in collect_refs(it["collection"], it.get("payload"))})
    pending_refs = await repository.changes_map(cs_id, ref_colls) if ref_colls else {}
    lote = {}
    for it in ordered:
        lote.setdefault(it["collection"], {})[it["entityId"]] = (
            {"op": "delete"} if it["op"] == "delete" else {"op": "upsert", "payload": it.get("payload") or {}})
    merged = {c: {**(pending_refs.get(c) or {}), **(lote.get(c) or {})} for c in set(pending_refs) | set(lote)}
    for it in ordered:
        it["payload"] = await _pruned_payload(cs, it["collection"], it["op"], it.get("payload"), merged)   # doc 88 §4
        await _cross_project_check(cs, it["collection"], it["entityId"], it["op"], it.get("payload"), merged)

    # Doc 83: reglas de naming (case del físico de columnas + override) ANTES
    # de unicidad/longitud — mismo orden que add_change.
    await _apply_naming_rules(cs["projectId"], ordered)

    upserts = [it for it in ordered if it["op"] == "upsert" and it["collection"] in _UNIQUE_COLLS]
    pending_all: dict[str, dict] = {}
    pub_tables: dict[str, list[dict]] = {}
    pub_cols: dict[str, list[dict]] = {}
    pub_schemas: dict[str, list[dict]] = {}
    max_len: dict[str, int] = {}
    if upserts:
        # Slices UNA vez por lote (el chequeo por ítem queda en memoria):
        # pendientes de las colecciones con unicidad + publicado relevante.
        pending_all = await repository.changes_map(cs_id, sorted({it["collection"] for it in upserts}))
        for it in upserts:
            p = it.get("payload") or {}
            if it["collection"] == "canonical_tables" and str(p.get("physicalName") or "").strip():
                pub_tables[it["entityId"]] = await repository.published(
                    "canonical_tables", _table_dup_filter(cs["projectId"], p))
            elif it["collection"] == "canonical_columns" and p.get("tableId") and p["tableId"] not in pub_cols:
                pub_cols[p["tableId"]] = await repository.published(
                    "canonical_columns", {"tableId": p["tableId"]})
            elif it["collection"] == "schemas" and str(p.get("name") or "").strip():
                name = str(p["name"]).strip()
                if name.lower() not in pub_schemas:
                    pub_schemas[name.lower()] = await repository.published(
                        "schemas", scoped(cs["projectId"], {"name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}}))
        for coll, scope in (("canonical_tables", "table"), ("canonical_columns", "column")):
            if any(it["collection"] == coll for it in upserts):
                max_len[coll] = int((await settings_service.get_naming_for(cs["projectId"], scope)).get("maxLength") or 0)

    for it in ordered:
        coll, eid, op = it["collection"], it["entityId"], it["op"]
        pending = pending_all.setdefault(coll, {})
        if op == "upsert" and coll in _UNIQUE_COLLS:
            p = it.get("payload") or {}
            if coll == "canonical_tables":
                pub = pub_tables.get(eid, [])
            elif coll == "canonical_columns":
                pub = pub_cols.get(p.get("tableId"), [])
            else:
                pub = pub_schemas.get(str(p.get("name") or "").strip().lower(), [])
            dup = validation.duplicate_error(coll, eid, p, pub, pending)
            if dup and coll == "canonical_tables" and await _table_name_grandfathered(cs["projectId"], eid, p):
                dup = None  # nombre legacy sin cambios (doc 50)
            if dup:
                raise DuplicateEntityError(dup)
            limit = max_len.get(coll, 0)
            name = str(p.get("physicalName") or "").strip()
            if limit and name and len(name) > limit:
                # Grandfather: el nombre EFECTIVO actual (pendiente evolutivo o
                # publicado por id — camino raro, sólo si excede) no se penaliza.
                current: str | None = None
                if eid in pending:
                    current = str((pending[eid].get("payload") or {}).get("physicalName") or "").strip() or None
                if current is None:
                    pub_self = await repository.published(coll, {"_id": eid})
                    if pub_self:
                        current = str(pub_self[0].get("physicalName") or "").strip() or None
                if current is None or current.casefold() != name.casefold():
                    raise NameTooLongError(
                        _too_long_msg("table" if coll == "canonical_tables" else "column", name, limit))
        # El pending evoluciona con CADA ítem (también deletes y colecciones
        # sin unicidad no cuestan nada): semántica del loop secuencial.
        pending[eid] = {"op": op} if op == "delete" else {"op": op, "payload": it.get("payload") or {}}

    updated = await repository.set_changes_bulk(cs_id, ordered)
    if updated is not None:
        return updated
    return "locked" if await repository.get(cs_id) else None


def _q_match(doc: dict, q: str) -> bool:
    """¿El doc matchea la búsqueda por nombre? (contains, case-insensitive).
    Mismos campos que el filtro Mongo de `_q_filter`. Puro."""
    needle = q.lower()
    return any(
        needle in str(doc.get(f) or "").lower()
        for f in ("physicalName", "logicalName", "name")
    )


def _q_filter(q: str) -> dict:
    """Filtro Mongo de búsqueda por nombre (regex escapado, case-insensitive)."""
    rx = {"$regex": re.escape(q), "$options": "i"}
    return {"$or": [{"physicalName": rx}, {"logicalName": rx}, {"name": rx}]}


async def effective(cs_id: str, collection: str,
                    table_id: str | None = None, ids: list[str] | None = None,
                    q: str | None = None, limit: int | None = None,
                    schema: str | None = None) -> list[dict] | None:
    """Estado efectivo (publicado + overlay del changeset) de una colección.

    `table_id`/`ids` acotan la lectura a un SLICE: sin filtro, un GET de
    canonical_columns con 300k documentos publicados mueve toda la colección
    por request. Con filtro, los `changes` del changeset también se acotan al
    slice (una columna nueva de OTRA tabla no debe colarse en la respuesta).

    `q`/`limit` (excluyentes con los slices): búsqueda server-side por nombre
    para los modales de catálogo — publicado filtrado+capado en Mongo, cambios
    del changeset filtrados por el MISMO criterio, y re-filtro post-overlay
    (un upsert puede renombrar la entidad y sacarla del match).

    `schema` (Database Explorer draft-aware, doc 25): tablas/vistas de UN
    esquema — mismo patrón que `q` pero filtrando por `schema` en vez de por
    nombre. Precede a `q`/`limit` (el Explorer pasa `schema`+`limit` juntos).

    Doc 70: `asof:<versionId>` (snapshot de una versión publicada) no tiene doc
    de changeset — el overlay lo resuelve `repository.changes_map`."""
    version_id = asof.asof_version_id(cs_id)
    project_id: str | None = None
    if version_id is None:
        cs = await repository.get(cs_id)
        if not cs:
            return None
        project_id = cs.get("projectId")
    changes = (await repository.changes_map(cs_id, [collection])).get(collection, {})

    async def _scoped(flt: dict | None) -> dict | None:
        # Doc 75 D6: defensa en profundidad — un slice publicado SIN clave de
        # entidad (`_id`/`tableId`) lleva el proyecto del changeset. Para
        # `asof:` la cabecera de la versión se lee sólo si hace falta.
        nonlocal project_id
        if collection not in PROJECT_SCOPED or (set(flt or {}) & {"_id", "tableId", "projectId"}):
            return flt
        if project_id is None and version_id is not None:
            target = await repository.get(version_id)
            project_id = (target or {}).get("projectId")
        return scoped(project_id, flt) if project_id else flt

    if schema is not None:
        # Bounded por esquema (como el reporting per-schema) + overlay del draft.
        # `in_slice` = publicado en el esquema; a los `changes` sólo dejamos los
        # que YA están en el slice o cuyo payload cae en este esquema (una tabla
        # nueva/movida al esquema). El POST-filtro por `schema` sobre el overlay
        # descarta lo que el draft SACÓ del esquema (rename de `schema`).
        #
        # `q` opcional COMBINADO (modal "New table · Import existing" pasa
        # schema+q+limit juntos): el filtro por nombre se aplica ADEMÁS del
        # esquema — en Mongo sobre el publicado y por-doc sobre cambios y overlay.
        # Sin `q` (Database Explorer) el comportamiento por-esquema queda intacto.
        flt = {"schema": schema, **(_q_filter(q) if q else {})}
        sort_field = "physicalName" if collection == "canonical_tables" else None
        pub = await repository.published(collection, await _scoped(flt), limit=limit, sort_field=sort_field)

        def _keep(doc: dict) -> bool:
            return doc.get("schema") == schema and (not q or _q_match(doc, q))

        in_slice = {d["id"] for d in pub}
        changes = {
            eid: ch for eid, ch in changes.items()
            if eid in in_slice or _keep(ch.get("payload") or {})
        }
        out = [d for d in overlay(pub, changes) if _keep(d)]
        out.sort(key=lambda d: str(d.get("physicalName") or d.get("name") or "").lower())
        return out[:limit] if limit else out

    if q is not None or limit is not None:
        # `limit` sin `q` = página inicial del modal (primeras N por nombre).
        # sort en la BD SOLO donde hay índice (canonical_tables.physicalName):
        # a escala, un orden sin índice sería full-scan.
        q = q or ""
        sort_field = "physicalName" if collection == "canonical_tables" else None
        pub = await repository.published(collection, await _scoped(_q_filter(q) if q else None),
                                         limit=limit, sort_field=sort_field)
        in_slice = {d["id"] for d in pub}
        changes = {
            eid: ch for eid, ch in changes.items()
            if eid in in_slice or _q_match(ch.get("payload") or {}, q)
        }
        out = [d for d in overlay(pub, changes) if _q_match(d, q)]
        out.sort(key=lambda d: str(d.get("physicalName") or d.get("logicalName") or d.get("name") or "").lower())
        return out[:limit] if limit else out

    flt: dict | None = None
    if ids is not None:
        flt = {"_id": {"$in": ids}}
    elif table_id is not None:
        # En relationships "por tabla" significa cualquiera de los extremos.
        # (Se consultan también los campos legacy source/target por si quedara
        # algún doc pre-backfill; el overlay+validate del read path normaliza.)
        if collection == "relationships":
            flt = {"$or": [{"parentTableId": table_id}, {"childTableId": table_id},
                           {"sourceTableId": table_id}, {"targetTableId": table_id}]}
        elif collection == "views":
            # Vistas versionadas (doc 20): "por tabla" = la tabla es fuente
            # (sourceTableIds contiene) o la base legacy (tableId).
            flt = {"$or": [{"tableId": table_id}, {"sourceTableIds": table_id}]}
        else:
            flt = {"tableId": table_id}
    pub = await repository.published(collection, await _scoped(flt))
    if flt is not None:
        in_slice = {d["id"] for d in pub}
        wanted = set(ids or [])

        def _in_scope(ch: dict) -> bool:
            p = ch.get("payload") or {}
            if table_id is None:
                return False
            if collection == "relationships":
                return table_id in (p.get("parentTableId"), p.get("childTableId"),
                                    p.get("sourceTableId"), p.get("targetTableId"))
            if collection == "views":
                src = p.get("sourceTableIds") or ([p.get("tableId")] if p.get("tableId") else [])
                return table_id in src
            return p.get("tableId") == table_id

        changes = {
            eid: ch for eid, ch in changes.items()
            if eid in in_slice or eid in wanted or _in_scope(ch)
        }
    return overlay(pub, changes)


async def submit(cs_id: str, actor: str, title: str | None = None, description: str | None = None,
                 reviewers: list[str] | None = None) -> dict | str | None:
    """Crea el publish request: guarda revisores/descripción y pasa a `submitted`.
    Sólo el OWNER ("forbidden" si no) y sólo desde `draft` (None si no): re-submitir
    un request ya en revisión pisaría título/revisores del envío anterior y podía
    resucitar a `submitted` uno decidido en paralelo — para cambiarlo, el owner lo
    retira primero (withdraw). Transición ATÓMICA draft→submitted con approvals
    reseteado (nada heredado de un ciclo de revisión anterior)."""
    cs = await repository.get(cs_id)
    if not cs or cs["status"] != "draft":
        return None
    if cs.get("owner") != actor:
        return "forbidden"
    await ensure_project_alive(cs)
    fields: dict = {"status": "submitted", "submittedAt": _now(), "approvals": {}}
    if title is not None:
        fields["title"] = title
    if description is not None:
        fields["description"] = description
    if reviewers is not None:
        fields["reviewers"] = reviewers
    # Doc 88 §6: cada envío es un ciclo del historial de solicitudes.
    fields["requests"] = open_cycle(cs, actor, fields["submittedAt"], title, description, reviewers)
    return await repository.transition(cs_id, "draft", fields)


# Doc 84 D1: qué hacer ante un publish bloqueado (viaja en `next` del detail).
PUBLISH_NEXT_STEP = ("The owner must withdraw the request, fix or remove the listed items, "
                     "and re-submit it for review.")


async def _publish_duplicate_items(project_id: str, changes: dict) -> list[dict]:
    """Re-chequeo COMPLETO de unicidad al publicar (spec 10 §9): cubre la
    carrera entre changesets concurrentes DEL MISMO PROYECTO (doc 75 D6).
    Queries acotadas: tablas por $or de nombres dentro del proyecto, columnas
    por $in de los tableIds tocados, esquemas por nombre dentro del proyecto.

    Doc 84 A1: devuelve la lista COMPLETA de choques como ítems estructurados
    (sin truncar) — {collection, kind, entityId, name, schema | tableId +
    tableName, where: production | request, message, existing: {id, name, …}}
    — para que el panel de bloqueo diga qué choca, con qué y dónde. `existing`
    se enriquece después con la versión que lo publicó (`_production_refs`)."""
    items: list[dict] = []
    seen: set[str] = set()

    def _push(item: dict) -> None:
        if item["message"] in seen:      # mismo dedupe que los mensajes de siempre
            return
        seen.add(item["message"])
        items.append(item)

    tbl_changes = changes.get("canonical_tables") or {}
    tbl_upserts = {
        eid: ch for eid, ch in tbl_changes.items()
        if ch.get("op") != "delete"
        and str((ch.get("payload") or {}).get("physicalName") or "").strip()
    }
    if tbl_upserts:
        flt = scoped(project_id, {"$or": [_table_name_filter(ch.get("payload") or {}) for ch in tbl_upserts.values()]})
        pub = await repository.published("canonical_tables", flt)
        pub_ids = {d.get("id") for d in pub}
        for eid, ch in tbl_upserts.items():
            p = ch.get("payload") or {}
            hit = validation.duplicate_hit("canonical_tables", eid, p, pub, tbl_changes)
            if hit and await _table_name_grandfathered(project_id, eid, p):
                hit = None  # nombre legacy sin cambios (doc 50)
            if hit:
                h = hit["hit"]
                _push({"collection": "canonical_tables", "kind": "table", "entityId": eid,
                       "name": str(p.get("physicalName") or "").strip(),
                       "schema": str(p.get("schema") or "").strip() or None,
                       "where": "production" if h.get("id") in pub_ids else "request",
                       "message": hit["message"],
                       "existing": {"id": h.get("id"), "name": h.get("physicalName"),
                                    "schema": str(h.get("schema") or h.get("sql_schema") or "").strip() or None}})

    col_changes = changes.get("canonical_columns") or {}
    col_upserts = {
        eid: ch for eid, ch in col_changes.items()
        if ch.get("op") != "delete" and (ch.get("payload") or {}).get("tableId")
    }
    if col_upserts:
        tids = sorted({(ch.get("payload") or {})["tableId"] for ch in col_upserts.values()})
        pub = await repository.published("canonical_columns", {"tableId": {"$in": tids}})
        pub_ids = {d.get("id") for d in pub}
        # Nombre de la tabla dueña: las del propio request + las publicadas.
        tnames = {teid: (tch.get("payload") or {}).get("physicalName")
                  for teid, tch in tbl_changes.items() if tch.get("payload")}
        missing = [t for t in tids if t not in tnames]
        if missing:
            for d in await repository.published("canonical_tables", {"_id": {"$in": missing}}):
                tnames[d["id"]] = d.get("physicalName")
        for eid, ch in col_upserts.items():
            p = ch.get("payload") or {}
            hit = validation.duplicate_hit("canonical_columns", eid, p, pub, col_changes)
            if hit:
                h = hit["hit"]
                _push({"collection": "canonical_columns", "kind": "column", "entityId": eid,
                       "name": str(p.get("physicalName") or "").strip(),
                       "tableId": p.get("tableId"), "tableName": tnames.get(p.get("tableId")),
                       "where": "production" if h.get("id") in pub_ids else "request",
                       "message": hit["message"],
                       "existing": {"id": h.get("id"), "name": h.get("physicalName"),
                                    "tableId": h.get("tableId"), "tableName": tnames.get(h.get("tableId"))}})

    sch_changes = changes.get("schemas") or {}
    sch_upserts = {
        eid: ch for eid, ch in sch_changes.items()
        if ch.get("op") != "delete"
        and str((ch.get("payload") or {}).get("name") or "").strip()
    }
    if sch_upserts:
        names = sorted({str((ch.get("payload") or {}).get("name") or "").strip()
                        for ch in sch_upserts.values()})
        flt = scoped(project_id, {"$or": [{"name": {"$regex": f"^{re.escape(n)}$", "$options": "i"}} for n in names]})
        pub = await repository.published("schemas", flt)
        pub_ids = {d.get("id") for d in pub}
        for eid, ch in sch_upserts.items():
            p = ch.get("payload") or {}
            hit = validation.duplicate_hit("schemas", eid, p, pub, sch_changes)
            if hit:
                h = hit["hit"]
                _push({"collection": "schemas", "kind": "schema", "entityId": eid,
                       "name": str(p.get("name") or "").strip(),
                       "where": "production" if h.get("id") in pub_ids else "request",
                       "message": hit["message"],
                       "existing": {"id": h.get("id"), "name": h.get("name")}})
    return items


async def _publish_duplicates(project_id: str, changes: dict) -> list[str]:
    """Mensajes de los choques (compat: bulk upload y tests). Ver `_publish_duplicate_items`."""
    return [it["message"] for it in await _publish_duplicate_items(project_id, changes)]


async def _production_refs(items: list[dict]) -> None:
    """Doc 84 A1: completa `existing` de los choques CONTRA PRODUCCIÓN con la
    ÚLTIMA versión publicada que tocó esa entidad — {version, owner,
    publishedAt} — a partir del mismo ledger del historial (doc 51). Best-
    effort: sin ledger (migración Erwin) o con error queda None; JAMÁS altera
    el error de publish que se está armando."""
    for it in items:
        ex = it.get("existing")
        if it.get("where") != "production" or not ex or not ex.get("id"):
            continue
        ex.update({"version": None, "owner": None, "publishedAt": None})
        try:
            changes = await repository.entity_changes(it["collection"], str(ex["id"]))
            headers = await repository.changesets_by_ids([str(c.get("csId") or "") for c in changes])
            events = history_events(changes, headers)
        except Exception:
            log.warning("production ref unavailable for publish conflict",
                        extra={"collection": it.get("collection"), "id": ex.get("id")})
            continue
        if events:
            last = max(events, key=lambda e: str(e.get("at") or ""))
            ex.update({"version": last.get("version"), "owner": last.get("userId"),
                       "publishedAt": last.get("at")})


def _duplicates_message(items: list[dict]) -> str:
    """Mensaje corto del bloqueo por duplicados (doc 84 D1): conteo por tipo
    contra producción + repetidos dentro del request + los 3 primeros detalles.
    Puro. La lista completa viaja en `items` (el panel la pinta entera)."""
    prod = [it for it in items if it.get("where") == "production"]
    req = [it for it in items if it.get("where") != "production"]
    parts: list[str] = []
    if prod:
        counts: dict[str, int] = {}
        for it in prod:
            kind = it.get("kind") or "entity"
            counts[kind] = counts.get(kind, 0) + 1
        pieces = [f"{n} {kind} name{'s' if n != 1 else ''}" for kind, n in counts.items()]
        parts.append(" and ".join(pieces) + " already exist" + ("s" if len(prod) == 1 else "")
                     + " in production")
    if req:
        parts.append(f"{len(req)} name{'s are' if len(req) != 1 else ' is'} repeated within the request")
    head = "Publish blocked: " + "; ".join(parts)
    msgs = [str(it.get("message") or "") for it in items[:3]]
    tail = " | ".join(m for m in msgs if m) + (f" | +{len(items) - 3} more" if len(items) > 3 else "")
    return f"{head} — {tail}" if tail else head


# ── Entidad `schemas` en el changeset (doc 18) ─────────────────────────────


def _overlay_by_schema(published: list[dict], coll_changes: dict | None, name: str) -> list[dict]:
    """Estado efectivo del slice `schema == name`: publicado + upserts del
    changeset (docs completos, PISAN al publicado homónimo), sin los deletes
    pendientes. Incluye entidades que el changeset MUDA hacia el esquema y
    excluye las que muda fuera. Puro."""
    docs = {d["id"]: d for d in published}
    for eid, ch in (coll_changes or {}).items():
        if ch.get("op") == "delete":
            docs.pop(eid, None)
        else:
            docs[eid] = {**(ch.get("payload") or {}), "id": eid}
    return [d for d in docs.values() if (d.get("schema") or "") == name]


async def _schema_usage(project_id: str, name: str, changes: dict) -> int:
    """Tablas + vistas EFECTIVAS (publicado + overlay del changeset) DEL PROYECTO
    que usan el esquema `name`."""
    total = 0
    for coll in ("canonical_tables", "views"):
        pub = await repository.published(coll, scoped(project_id, {"schema": name}))
        total += len(_overlay_by_schema(pub, changes.get(coll), name))
    return total


async def _publish_schema_deletes(project_id: str, changes: dict) -> list[str]:
    """Guard del publish (doc 18): un delete de esquema sólo publica si el
    esquema queda VACÍO en el estado efectivo — el propio changeset puede
    vaciarlo (borrando/mudando sus tablas) en el mismo request."""
    per = changes.get("schemas") or {}
    delete_ids = [eid for eid, ch in per.items() if ch.get("op") == "delete"]
    if not delete_ids:
        return []
    pub = await repository.published("schemas", {"_id": {"$in": delete_ids}})
    names = {d["id"]: d.get("name") for d in pub}
    errors: list[str] = []
    for eid in delete_ids:
        name = str(names.get(eid) or "").strip()
        if not name:
            continue
        used = await _schema_usage(project_id, name, changes)
        if used:
            errors.append(f"Schema {name} still has {used} table(s)/view(s)")
    return errors


async def rename_schema(cs_id: str, actor: str, schema_id: str, new_name: str) -> dict | str | None:
    """Renombra un esquema DENTRO del draft: upsert del schema con el nombre
    nuevo + un upsert (doc COMPLETO, schema reemplazado) por cada tabla/vista
    EFECTIVA que lo usa. Server-side a propósito: el effective de tablas es
    potencialmente enorme para el cliente, y reusar `add_change` hereda los
    guards de owner/draft/validación/unicidad. Pre-flight de duplicados ANTES
    de grabar nada (no deja un draft renombrado a medias). Devuelve
    {tables, views} o la semántica de add_change (None/"forbidden"/"locked")."""
    err = schemas_service.name_error(new_name)
    if err:
        raise InvalidPayloadError(err)
    new_name = new_name.strip()
    cs = await repository.get(cs_id)
    if not cs:
        return None
    pid = cs["projectId"]
    rows = await effective(cs_id, "schemas", ids=[schema_id])
    if rows is None:
        return None
    cur = next((r for r in rows if r.get("id") == schema_id), None)
    if cur is None:
        return None
    old_name = str(cur.get("name") or "").strip()

    counts = {"tables": 0, "views": 0}
    tables: list[dict] = []
    views: list[dict] = []
    if old_name and old_name != new_name:
        changes = await repository.changes_map(cs_id, ["canonical_tables", "views"])
        tables = _overlay_by_schema(
            await repository.published("canonical_tables", scoped(pid, {"schema": old_name})),
            changes.get("canonical_tables"), old_name)
        views = _overlay_by_schema(
            await repository.published("views", scoped(pid, {"schema": old_name})),
            changes.get("views"), old_name)
        # Pre-flight: si alguna tabla renombrada chocara con una homónima ya
        # existente en el esquema destino, se rechaza ENTERO con 409.
        for doc in tables:
            payload = {**{k: v for k, v in doc.items() if k != "id"}, "schema": new_name}
            dup = await _duplicate_error(cs, "canonical_tables", doc["id"], "upsert", payload)
            if dup:
                raise DuplicateEntityError(dup)

    res = await add_change(cs_id, actor, "schemas", schema_id, "upsert",
                           {**{k: v for k, v in cur.items() if k != "id"}, "name": new_name})
    if res is None or isinstance(res, str):
        return res
    for coll, key, docs in (("canonical_tables", "tables", tables), ("views", "views", views)):
        for doc in docs:
            payload = {**{k: v for k, v in doc.items() if k != "id"}, "schema": new_name}
            r = await add_change(cs_id, actor, coll, doc["id"], "upsert", payload)
            if r is None or isinstance(r, str):
                return r
            counts[key] += 1
    return counts


async def schema_impact(cs_id: str, schema_id: str) -> dict | None:
    """Impacto EFECTIVO de tocar un esquema (doc 18 v2, para el confirm del
    front ANTES de guardar): cuántas tablas y vistas usan su nombre, y en
    cuántos canvases aparecen (subject_areas cuyo tableIds interseca las
    tablas del esquema o las FUENTES de sus vistas — una vista se muestra en
    el canvas vía sus tablas fuente)."""
    pid = await _project_of_changeset(cs_id)
    if pid is None:
        return None
    rows = await effective(cs_id, "schemas", ids=[schema_id])
    if rows is None:
        return None
    cur = next((r for r in rows if r.get("id") == schema_id), None)
    if cur is None:
        return None
    name = str(cur.get("name") or "").strip()
    if not name:
        return {"name": name, "tables": 0, "views": 0, "canvases": 0}
    changes = await repository.changes_map(
        cs_id, ["canonical_tables", "views", "subject_areas"])
    tables = _overlay_by_schema(
        await repository.published("canonical_tables", scoped(pid, {"schema": name})),
        changes.get("canonical_tables"), name)
    views = _overlay_by_schema(
        await repository.published("views", scoped(pid, {"schema": name})),
        changes.get("views"), name)
    touched = {d["id"] for d in tables}
    for v in views:
        srcs = v.get("sourceTableIds") or ([v["tableId"]] if v.get("tableId") else [])
        touched.update(srcs)
    canvases = 0
    if touched:
        # subject_areas completa (28 canvases hoy): mismo tradeoff aceptado que
        # el effective de estructura (doc 16 §5 pendientes).
        sas = overlay(await repository.published("subject_areas", scoped(pid)),
                      changes.get("subject_areas") or {})
        canvases = sum(1 for sa in sas if touched & set(sa.get("tableIds") or []))
    return {"name": name, "tables": len(tables), "views": len(views), "canvases": canvases}


async def delete_schema_in_changeset(cs_id: str, actor: str, schema_id: str):
    """Registra el delete del esquema en el draft SÓLO si su estado efectivo
    está vacío. Devuelve ("in-use", n) si tiene tablas/vistas efectivas, o la
    semántica de add_change (dict / "forbidden" / "locked" / None)."""
    rows = await effective(cs_id, "schemas", ids=[schema_id])
    if rows is None:
        return None
    cur = next((r for r in rows if r.get("id") == schema_id), None)
    if cur is None:
        return None
    name = str(cur.get("name") or "").strip()
    if name:
        pid = await _project_of_changeset(cs_id)
        changes = await repository.changes_map(cs_id, ["canonical_tables", "views"])
        used = await _schema_usage(pid, name, changes)
        if used:
            return ("in-use", used)
    return await add_change(cs_id, actor, "schemas", schema_id, "delete", None)


async def _apply_and_finalize(cs: dict, fields: dict, submitted_at: str | None) -> dict | None:
    """Cierra el request y aplica el changeset a las colecciones publicadas
    (doc 75: todo dentro del proyecto del changeset). Orden y garantías:

    1. RECLAMA el estado con una transición atómica `submitted → fields`,
       condicionada ADEMÁS al `submittedAt` del envío que el revisor decidió:
       un withdraw → edit → resubmit del owner en el medio cambia submittedAt y
       el claim tardío falla (None) — sin esto, el claim por-status podía
       aprobar un envío que nadie revisó (ABA).
    2. Lee los cambios DESPUÉS de reclamar y los filtra a `at <= submittedAt`
       (`changes_in_cycle`): fuera de draft `add_change` rechaza, y la ventana
       de su compensación no puede colar una escritura tardía al publish — el
       set aplicado es exactamente el enviado.
    3. Gate de validación autoritativo: si algún payload no valida contra su
       modelo (datos legacy pre-validación), REVIERTE el claim (limpiando
       decisiones, como withdraw) y levanta `InvalidPayloadError` (422).
    4. Si el apply/cascada FALLA (throttling o timeout), devuelve el
       request a `submitted` y re-lanza: el apply es idempotente (upserts por
       _id), re-aprobar reintenta y converge. Si el PROCESO muere a mitad, el
       doc queda `approved` sin `appliedAt` — detectable, y recuperable con
       `scripts/reapply_changeset.py`.
    5. `appliedAt` se estampa recién con el apply completo: es el marcador de
       "esta versión SÍ está en producción" (lo usa current_production).
    """
    cs_id = cs["id"]
    pid = cs["projectId"]
    final = await repository.transition(
        cs_id, "submitted", fields, expect={"submittedAt": submitted_at}
    )
    if final is None:
        return None
    changes = changes_in_cycle(await repository.changes_map(cs_id), submitted_at)
    # Doc 88 §6: al revertir el claim, el ciclo vuelve a `pending` (el cierre
    # `approved` del historial se deshace junto con el estado).
    _revert = {"status": "submitted", "reviewedBy": None, "reviewedAt": None, "approvals": {},
               "requests": list(cs.get("requests") or [])}
    invalid = validation.validate_changes(changes)
    if invalid:
        await repository.transition(cs_id, fields.get("status", "approved"), _revert)
        raise InvalidPayloadError(
            "Publish blocked: the request contains changes that no longer validate — "
            + " | ".join(invalid[:5]) + (f" | +{len(invalid) - 5} more" if len(invalid) > 5 else ""),
            items=[{"kind": "change", "message": m} for m in invalid], next_step=PUBLISH_NEXT_STEP)
    # Re-chequeo de unicidad (spec 10 §9): otro changeset pudo publicar el
    # nombre entre el add_change y este apply. Mismo protocolo que el gate de
    # validación: revierte el claim y la producción queda intacta.
    dups = await _publish_duplicate_items(pid, changes)
    if dups:
        await repository.transition(cs_id, fields.get("status", "approved"), _revert)
        # Doc 84 A1: la lista COMPLETA + con qué versión choca (best-effort).
        # El bloqueo en sí no cambia: mismo guard, mismo momento, mismo 409.
        await _production_refs(dups)
        raise DuplicateEntityError(_duplicates_message(dups), items=dups, next_step=PUBLISH_NEXT_STEP)
    # Guard de esquemas (doc 18): un delete de esquema con tablas/vistas
    # efectivas no publica — mismo protocolo (revierte el claim, 409).
    in_use = await _publish_schema_deletes(pid, changes)
    if in_use:
        await repository.transition(cs_id, fields.get("status", "approved"), _revert)
        raise SchemaInUseError(
            "Publish blocked: the request deletes schemas that are still in use — "
            + " | ".join(in_use[:5]) + (f" | +{len(in_use) - 5} more" if len(in_use) > 5 else ""),
            items=[{"kind": "schema", "message": m} for m in in_use], next_step=PUBLISH_NEXT_STEP)
    # Doc 75 I2 (gate autoritativo): ninguna referencia del plan cruza proyectos.
    # Doc 88 §4: los canvases se publican PODADOS de los miembros que el mismo
    # request borra (la poda muta el plan en memoria antes del apply).
    try:
        for coll, per in changes.items():
            for eid, ch in per.items():
                ch["payload"] = await _pruned_payload(cs, coll, ch.get("op"), ch.get("payload"), changes)
                await _cross_project_check(cs, coll, eid, ch.get("op"), ch.get("payload"), changes)
    except CrossProjectError:
        await repository.transition(cs_id, fields.get("status", "approved"), _revert)
        raise
    try:
        plan = apply_plan(changes)
        # Rollback (doc 16 §5d): capturar y estampar la imagen PREVIA de cada
        # entidad ANTES de aplicar — después del apply ya no existe el "antes".
        # `store_before_images` es no-op sobre cambios ya estampados (re-apply).
        await repository.store_before_images(
            cs_id, await repository.capture_before_images(plan))
        counts = await repository.apply_changes(plan)
        if any(coll == "projects" and op == "delete" for coll, _eid, op, _p in plan):
            # Doc 75 D5: el proyecto se borró en esta versión → cascada soft-delete
            # de todo lo suyo. Idempotente; si falla, la request vuelve a
            # `submitted` (except de abajo) y re-aprobar converge.
            counts["cascade"] = await projects_repo.cascade_delete(pid, cs_id)
            log.warning("project deleted by changeset", extra={"cs_id": cs_id, "project": pid})
    except Exception:
        log.exception("changeset apply FAILED — request devuelto a revisión",
                      extra={"cs_id": cs_id})
        await repository.transition(cs_id, fields.get("status", "approved"), _revert)
        raise
    final = await repository.set_status(cs_id, {"appliedAt": _now()}) or final
    # El publish es LA operación de escritura crítica: sin este log no hay forma
    # de diagnosticar en producción qué aplicó (o dejó de aplicar) una versión.
    log.info("changeset applied", extra={"cs_id": cs_id, "applied": counts})
    return final


def rollback_plan(changes: dict) -> tuple[list[dict], list[str]]:
    """Cambios INVERSOS de un changeset aplicado, a partir de las imágenes
    previas capturadas en el publish. Puro.

    Por entidad tocada: `before=None` (no existía) → delete; `before=doc` →
    upsert con el doc previo completo. Devuelve (inversos, faltantes) —
    `faltantes` = cambios SIN imagen previa (publicados antes de la feature):
    con cualquiera, el rollback no es reconstruible y se aborta."""
    inverse: list[dict] = []
    missing: list[str] = []
    for coll, per in (changes or {}).items():
        for eid, ch in per.items():
            if not ch.get("beforeAt"):
                missing.append(f"{coll}/{eid}")
                continue
            before = ch.get("before")
            if before is None:
                inverse.append({"collection": coll, "entityId": eid,
                                "op": "delete", "payload": None})
            else:
                inverse.append({"collection": coll, "entityId": eid,
                                "op": "upsert",
                                "payload": {k: v for k, v in before.items() if k != "id"}})
    return inverse, missing


async def rollback(cs_id: str, actor: str) -> dict | str | None:
    """Crea un DRAFT que RESTAURA el modelo al estado de la versión `cs_id`
    (cualquier versión publicada, no sólo la última): deshace TODAS las versiones
    publicadas DESPUÉS de la objetivo, componiendo sus cambios inversos. El draft
    pasa por el flujo normal (submit → review → approve): el rollback también se
    revisa, se audita y re-valida (unicidad/payloads) — igual que cualquier publish.

    Composición: se recorren las versiones a deshacer del MÁS RECIENTE al más
    VIEJO y por entidad gana el inverso de la versión MÁS CERCANA a la objetivo
    (su imagen previa = el estado tal como quedó en la objetivo). Publicar el
    draft deja el modelo idéntico a como estaba al aplicar `cs_id`.

    Devuelve el draft creado, o: None (no existe) · "not-applied" (no es una
    versión publicada) · "no-before" (alguna versión posterior se publicó antes
    de la captura de imágenes previas ⇒ no reconstruible) · "empty" (la objetivo
    ya es la producción actual: no hay nada posterior que deshacer)."""
    cs = await repository.get(cs_id)
    if not cs:
        return None
    if cs.get("status") != "approved" or not cs.get("appliedAt"):
        return "not-applied"
    await ensure_project_alive(cs)
    # Versiones publicadas DESPUÉS de la objetivo (latest→oldest). Ninguna ⇒ la
    # objetivo ES la producción actual: no hay rollback que hacer.
    after = await repository.applied_after(cs["projectId"], cs["appliedAt"])   # doc 75 I6
    if not after:
        return "empty"
    # Inversos deduplicados por entidad; al iterar latest→oldest, el inverso de
    # la versión MÁS VIEJA (más cercana a la objetivo) queda de ÚLTIMO y gana.
    inverse_by_key: dict[tuple[str, str], dict] = {}
    missing: list[str] = []
    for ver in after:
        inv, miss = rollback_plan(await repository.changes_map(ver["id"]))
        missing.extend(miss)
        for ch in inv:
            inverse_by_key[(ch["collection"], ch["entityId"])] = ch
    if missing:
        return "no-before"
    inverse = list(inverse_by_key.values())
    if not inverse:
        return "empty"
    label = cs.get("versionLabel") or cs.get("title") or cs_id[:8]
    n_ver = len(after)
    # Doc 65: el draft de restauración nace con etiqueta autoincremental (como
    # snapshot — antes quedaba sin label y la fila salía "—") y con la
    # PROCEDENCIA estructurada de la versión restaurada: el UI muestra
    # "Restored from vN" en Home/historial sin parsear títulos.
    existing = [c.get("versionLabel") for c in await repository.list_summaries(cs["projectId"])]
    draft = await repository.create(
        f"Restore to {label}", actor,
        extra={"description": f"Restores the model to the state of {label}: undoes "
                              f"{n_ver} later version(s) across {len(inverse)} entities.",
               "versionLabel": next_version_label(existing),
               "projectId": cs["projectId"],
               "restoredFrom": {"csId": cs["id"], "versionLabel": cs.get("versionLabel"),
                                "appliedAt": cs.get("appliedAt")}})
    for ch in inverse:
        await repository.set_change(draft["id"], ch["collection"], ch["entityId"],
                                    ch["op"], ch["payload"])
    return await repository.get(draft["id"])


async def review(cs_id: str, actor: str, decision: str, note: str | None) -> dict | str | None:
    """Decisión de un revisor asignado. Devuelve:
    - dict: el changeset actualizado (registrada la decisión / aplicado / rechazado).
    - "forbidden": el actor NO está en `reviewers` (→ 403 en el router).
    - None: el changeset no existe o no está en revisión.
    Política: TODOS los reviewers aprobaron → se aplica (approved); alguno
    rechaza → rejected; si aún faltan, queda en submitted con la decisión guardada.
    """
    cs = await repository.get(cs_id)
    if not cs or cs["status"] != "submitted":
        return None
    if not is_assigned(cs.get("reviewers", []), actor):
        return "forbidden"
    # Doc 88 §5: rechazar exige motivo (antes de registrar nada).
    if decision == "reject" and not (note or "").strip():
        raise RejectionReasonRequired("A rejection must include the reason: tell the owner what to fix.")
    await ensure_project_alive(cs)

    # Decisión con $set atómico en `approvals.<actor>`: decisiones concurrentes
    # de otros revisores no se pisan. El outcome se computa sobre el documento
    # RESULTANTE (todas las decisiones ya registradas).
    entry = record_approval({}, actor, decision, note)[actor]
    updated = await repository.set_approval(cs_id, actor, entry)
    if not updated:
        return None  # dejó de estar en revisión entre el get y la decisión
    outcome = approval_outcome(updated.get("reviewers", []), updated.get("approvals", {}))

    if outcome == "approved":
        return await _apply_and_finalize(
            updated, {"status": "approved", "reviewedBy": actor, "reviewedAt": _now(),
                      "requests": close_cycle(updated, "approved", actor, decisions=updated.get("approvals"))},
            submitted_at=updated.get("submittedAt"),
        )
    if outcome == "rejected":
        # Doc 88 §6: el rechazo devuelve la versión DIRECTO a `draft` (el owner
        # sigue editando sin pasar por Reopen); la solicitud, su motivo y las
        # decisiones quedan en el historial. Guard atómico también en el cierre
        # negativo: un withdraw concurrente (submitted→draft) no debe quedar
        # pisado por un rechazo tardío, y el expect por submittedAt evita
        # rechazar un RE-envío que el revisor nunca vio (ABA withdraw→resubmit).
        return await repository.transition(
            cs_id, "submitted",
            {"status": "draft", "approvals": {}, "submittedAt": None,
             "reviewedBy": actor, "reviewedAt": _now(), "reviewNote": note,
             "requests": close_cycle(updated, "rejected", actor, note, decisions=updated.get("approvals"))},
            expect={"submittedAt": updated.get("submittedAt")},
        )
    # Aún faltan aprobaciones: la decisión ya quedó registrada; sigue en revisión.
    return updated


async def withdraw(cs_id: str, actor: str) -> dict | str | None:
    """Retira un publish request en revisión: `submitted → draft`, para seguir
    editando y re-enviarlo después. Sólo el OWNER puede retirarlo ("forbidden"
    si no). Las decisiones ya registradas se INVALIDAN (approvals = {}): lo que
    se re-envíe puede diferir de lo que los revisores vieron. Guard atómico:
    None si dejó de estar en revisión (p.ej. un revisor decidió en paralelo)."""
    cs = await repository.get(cs_id)
    if not cs or cs["status"] != "submitted":
        return None
    if cs.get("owner") != actor:
        return "forbidden"
    return await repository.transition(
        cs_id, "submitted",
        {"status": "draft", "approvals": {}, "submittedAt": None,
         "requests": close_cycle(cs, "withdrawn", actor)},          # doc 88 §6
    )


async def reopen(cs_id: str, actor: str) -> dict | str | None:
    """Reabre un request RECHAZADO: `rejected → draft`, para corregir la versión
    y re-enviarla. El reject NUNCA borra la versión; esta es la vuelta a edición
    del owner (simétrica al withdraw, que aplica antes de la decisión). Sólo el
    OWNER ("forbidden" si no). Limpia decisiones y metadata de review: lo que se
    re-envíe se evalúa de cero. None si ya no está en rejected."""
    cs = await repository.get(cs_id)
    if not cs or cs["status"] != "rejected":
        return None
    if cs.get("owner") != actor:
        return "forbidden"
    await ensure_project_alive(cs)
    history = cs.get("requests") or []
    fields = {"status": "draft", "approvals": {}, "submittedAt": None,
              "reviewedBy": None, "reviewedAt": None, "reviewNote": None}
    if not history or history[-1].get("outcome") == "pending":
        # Doc 88 §6: doc rechazado ANTES del historial — se sintetiza el ciclo
        # para que la solicitud (y su motivo) no se pierdan al reabrir.
        fields["requests"] = close_cycle(cs, "rejected", cs.get("reviewedBy"), cs.get("reviewNote"),
                                         decisions=cs.get("approvals"))
    return await repository.transition(cs_id, "rejected", fields)


async def add_comment(cs_id: str, actor: str, text: str) -> dict | None:
    """Agrega `{author, text, at}` al hilo con `$push` atómico (comentarios
    concurrentes no se pisan)."""
    return await repository.push_comment(cs_id, {"author": actor, "text": text, "at": _now()})


def build_diff_tree(collections: dict, changes: dict, published: dict,
                    sas: list[dict], projects: list[dict], folders: list[dict]) -> dict:
    """Jerarquía Proyecto→Folder→Canvas→Esquema→Tabla→Columnas (+Vistas) de los
    cambios de tablas/columnas/vistas, para el vistazo de la revisión (p5).

    Devuelve `{tree, orphans, structure}`:
    - `tree`: nodos anidados por la jerarquía (una tabla en N canvases aparece
      en cada uno; las vistas cuelgan del/los canvas de sus fuentes).
    - `orphans`: tablas/vistas cambiadas sin canvas (no importadas a ningún ER).
    - `structure`: cambios de ESTRUCTURA (projects/folders/subject_areas/schemas
      como entidades) — su propio bloque, no encajan en el árbol de datos.
    Puro."""
    proj_name = {p["id"]: (p.get("name") or p["id"]) for p in projects}
    folder_by = {f["id"]: f for f in folders}
    sa_by_id = {sa["id"]: sa for sa in sas}

    def _kinds(coll: str) -> dict:
        b = collections.get(coll) or {}
        return {e["id"]: verb for verb in ("added", "edited", "deleted") for e in b.get(verb, [])}

    tbl_kind, col_kind, view_kind = _kinds("canonical_tables"), _kinds("canonical_columns"), _kinds("views")
    # Doc 84 B2: verbo de las entidades de ESTRUCTURA para marcarlas EN el árbol
    # (carpeta/canvas/proyecto por id; esquema por NOMBRE — el nodo del árbol es
    # el nombre del esquema y la entidad versionada es su uuid). `in_tree`
    # recuerda cuáles quedaron representadas para que la sección «Structure
    # changes» las marque (`inTree`) y el front no las repita.
    fold_kind, sa_kind, proj_kind = _kinds("folders"), _kinds("subject_areas"), _kinds("projects")
    schema_mark: dict[str, tuple[str, str]] = {}
    for verb in ("added", "edited", "deleted"):
        for e in (collections.get("schemas") or {}).get(verb, []):
            nm = str(e.get("name") or "").strip().lower()
            if nm:
                schema_mark[nm] = (verb, e["id"])
    in_tree: set[tuple[str, str]] = set()

    # Meta efectiva de tablas (nombre/esquema aun si sólo cambiaron columnas).
    tmeta = {d["id"]: d for d in published.get("canonical_tables", [])}
    for eid, ch in (changes.get("canonical_tables") or {}).items():
        if ch.get("payload"):
            tmeta[eid] = {**ch["payload"], "id": eid}

    # Columnas cambiadas agrupadas por tableId.
    cmeta = {d["id"]: d for d in published.get("canonical_columns", [])}
    cols_by_table: dict[str, list] = {}
    for eid, verb in col_kind.items():
        p = ((changes.get("canonical_columns") or {}).get(eid) or {}).get("payload") or {}
        tid = p.get("tableId") or (cmeta.get(eid) or {}).get("tableId") or "__none__"
        nm = p.get("physicalName") or p.get("logicalName") or (cmeta.get(eid) or {}).get("physicalName") or eid
        # Doc 84 B4: el tipo viaja en la fila (se escanea sin abrir cada columna).
        cols_by_table.setdefault(tid, []).append({
            "type": "column", "id": eid, "name": nm, "change": verb,
            "dataType": p.get("dataType") or (cmeta.get(eid) or {}).get("dataType")})

    # Vistas cambiadas.
    vmeta = {d["id"]: d for d in published.get("views", [])}
    view_entries = []
    for eid, verb in view_kind.items():
        p = ((changes.get("views") or {}).get(eid) or {}).get("payload") or vmeta.get(eid) or {}
        srcs = p.get("sourceTableIds") or ([p.get("tableId")] if p.get("tableId") else [])
        view_entries.append({"id": eid, "name": p.get("name") or eid, "change": verb,
                             "schema": p.get("schema") or "—", "sources": srcs})

    # Canvas por tabla (membership efectiva).
    sa_of: dict[str, list] = {}
    for sa in sas:
        for tid in sa.get("tableIds") or []:
            sa_of.setdefault(tid, []).append(sa)

    nested: dict = {}

    def slot(proj_id, folder_id, sa_id, schema) -> dict:
        p = nested.setdefault(proj_id, {})
        f = p.setdefault(folder_id or "__root__", {})
        s = f.setdefault(sa_id, {})
        return s.setdefault(schema or "—", {"tables": {}, "views": {}})

    # Tablas cambiadas + tablas con columnas cambiadas.
    orphan_tables, orphan_views = [], []
    all_table_ids = set(tbl_kind) | {tid for tid in cols_by_table if tid != "__none__"}
    for tid in all_table_ids:
        tname = (tmeta.get(tid) or {}).get("physicalName") or (tmeta.get(tid) or {}).get("logicalName") or tid
        node = {"type": "table", "id": tid, "name": tname, "change": tbl_kind.get(tid),
                "children": list(cols_by_table.get(tid, []))}
        canvases = sa_of.get(tid)
        if not canvases:
            orphan_tables.append(node)
            continue
        for sa in canvases:
            schema = (tmeta.get(tid) or {}).get("schema") or "—"
            slot(sa.get("projectId"), sa.get("folderId"), sa["id"], schema)["tables"].setdefault(tid, dict(node))

    # Vistas: bajo el/los canvas de sus fuentes; sin fuentes en canvas → orphan.
    for v in view_entries:
        seen_sa: set[str] = set()
        for src in v["sources"]:
            for sa in sa_of.get(src, []):
                if sa["id"] in seen_sa:
                    continue
                seen_sa.add(sa["id"])
                slot(sa.get("projectId"), sa.get("folderId"), sa["id"], v["schema"])["views"][v["id"]] = \
                    {"type": "view", "id": v["id"], "name": v["name"], "change": v["change"]}
        if not seen_sa:
            orphan_views.append({"type": "view", "id": v["id"], "name": v["name"], "change": v["change"]})

    # Serializar a lista de nodos (ordenados por nombre; tablas antes que vistas).
    tree = []
    for proj_id, folders_map in nested.items():
        f_children = []
        for folder_id, sas_map in folders_map.items():
            c_children = []
            for sa_id, schemas_map in sas_map.items():
                s_children = []
                for schema, groups in schemas_map.items():
                    kids = list(groups["tables"].values()) + list(groups["views"].values())
                    kids.sort(key=lambda n: (n["type"] != "table", (n.get("name") or "").lower()))
                    snode = {"type": "schema", "id": schema, "name": schema, "children": kids}
                    mark = schema_mark.get(str(schema or "").strip().lower())
                    if mark:
                        snode["change"], snode["entityId"] = mark
                        in_tree.add(("schemas", mark[1]))
                    s_children.append(snode)
                s_children.sort(key=lambda n: (n["name"] or "").lower())
                cnode = {"type": "canvas", "id": sa_id,
                         "name": (sa_by_id.get(sa_id) or {}).get("name") or sa_id, "children": s_children}
                if sa_kind.get(sa_id):
                    cnode["change"], cnode["entityId"] = sa_kind[sa_id], sa_id
                    in_tree.add(("subject_areas", sa_id))
                c_children.append(cnode)
            c_children.sort(key=lambda n: (n["name"] or "").lower())
            fname = "— (project root)" if folder_id == "__root__" else ((folder_by.get(folder_id) or {}).get("name") or folder_id)
            fnode = {"type": "folder", "id": folder_id, "name": fname, "children": c_children}
            if folder_id != "__root__" and fold_kind.get(folder_id):
                fnode["change"], fnode["entityId"] = fold_kind[folder_id], folder_id
                in_tree.add(("folders", folder_id))
            f_children.append(fnode)
        f_children.sort(key=lambda n: (n["name"] or "").lower())
        pnode = {"type": "project", "id": proj_id or "—", "name": proj_name.get(proj_id) or proj_id or "—",
                 "children": f_children}
        if proj_id and proj_kind.get(proj_id):
            pnode["change"], pnode["entityId"] = proj_kind[proj_id], proj_id
            in_tree.add(("projects", proj_id))
        tree.append(pnode)
    tree.sort(key=lambda n: (n["name"] or "").lower())

    STRUCT = {"projects": "Project", "folders": "Folder", "subject_areas": "Canvas", "schemas": "Schema"}
    structure = []
    for coll, label in STRUCT.items():
        b = collections.get(coll) or {}
        for verb in ("added", "edited", "deleted"):
            for e in b.get(verb, []):
                structure.append({"type": coll, "id": e["id"], "name": e.get("name") or e["id"],
                                  "kind": label, "change": verb,
                                  "inTree": (coll, e["id"]) in in_tree})

    return {"tree": tree, "orphans": orphan_tables + orphan_views, "structure": structure}


async def diff(cs_id: str) -> dict | None:
    """Diff estructurado por colección (added/edited/deleted con nombres +
    flag de conflicto vs producción) + impacto.

    Alimenta a `structured_diff` (puro) con SLICES por id: sólo los documentos
    publicados de las entidades tocadas, las relaciones que tocan esas tablas y
    los nombres de las tablas afectadas vía relaciones — cargar las 6
    colecciones completas por request no escala a 15k tablas."""
    cs = await repository.get(cs_id)
    if not cs:
        return None
    pid = cs["projectId"]
    changes = await repository.changes_map(cs_id)

    published: dict[str, list[dict]] = {}
    for col in VERSIONED:
        eids = list((changes.get(col) or {}).keys())
        published[col] = await repository.published(col, {"_id": {"$in": eids}}) if eids else []

    # Tablas tocadas: cambios directos de tabla + tableId de columnas cambiadas
    # (del payload, o del documento publicado para deletes sin payload).
    touched: set[str] = set((changes.get("canonical_tables") or {}).keys())
    col_meta = {d["id"]: d for d in published["canonical_columns"]}
    for eid, ch in (changes.get("canonical_columns") or {}).items():
        tid = ((ch.get("payload") or {}).get("tableId")) or (col_meta.get(eid) or {}).get("tableId")
        if tid:
            touched.add(tid)

    relationships: list[dict] = []
    if touched:
        t = sorted(touched)
        # `scoped()` NO es decorativo: `published()` exige alcance en el PRIMER
        # nivel del filtro (doc 75 D1) y `$or` no cuenta — sin el proyecto acá,
        # cada request que tocara una tabla moría con MissingProjectError → 500
        # en `GET /changesets/{id}/diff` y la revisión no cargaba (2026-09-09).
        relationships = await repository.published(
            "relationships",
            scoped(pid, {"$or": [{"parentTableId": {"$in": t}}, {"childTableId": {"$in": t}},
                                 {"sourceTableId": {"$in": t}}, {"targetTableId": {"$in": t}}]})
        )

    # Nombres de las tablas AFECTADAS vía relaciones (fuera del slice de tocadas).
    known = {d["id"] for d in published["canonical_tables"]}
    rel_tids = ({r.get("parentTableId") or r.get("targetTableId") for r in relationships}
                | {r.get("childTableId") or r.get("sourceTableId") for r in relationships})
    missing = [tid for tid in rel_tids if tid and tid not in known]
    if missing:
        published["canonical_tables"] = published["canonical_tables"] + await repository.published(
            "canonical_tables", {"_id": {"$in": missing}}
        )

    # Meta de TODAS las tablas tocadas (incl. las que sólo tienen cambios de
    # columna) para poder ubicarlas en la jerarquía de la revisión.
    known2 = {d["id"] for d in published["canonical_tables"]}
    need = [tid for tid in touched if tid not in known2]
    if need:
        published["canonical_tables"] = published["canonical_tables"] + await repository.published(
            "canonical_tables", {"_id": {"$in": need}})

    result = structured_diff(changes, published, relationships, baseline=cs.get("createdAt"))
    # Doc 84 A3: último cambio grabado en el request (el autor es el owner).
    result["lastChangeAt"] = max(
        (str(ch.get("at")) for per in changes.values() for ch in (per or {}).values() if ch.get("at")),
        default=None)
    # Jerarquía Proyecto→Folder→Canvas→Esquema→Tabla→Columnas (+Vistas) +
    # La
    # estructura es chica (proyectos/folders/canvases) → overlay completo.
    sas = overlay(await repository.published("subject_areas", scoped(pid)), changes.get("subject_areas") or {})
    pub_projects = await repository.published("projects", {"_id": pid})
    projs = overlay(pub_projects, changes.get("projects") or {})
    flds = overlay(await repository.published("folders", scoped(pid)), changes.get("folders") or {})
    tree = build_diff_tree(result["collections"], changes, published, sas, projs, flds)
    result["tree"] = tree["tree"]
    result["orphans"] = tree["orphans"]
    result["structure"] = tree["structure"]
    # Doc 75 D5/D20: un delete de `projects` es un cambio CRÍTICO — el revisor
    # ve qué desaparece (conteos vivos del proyecto) antes de aprobar.
    del_pid = next((eid for eid, ch in (changes.get("projects") or {}).items()
                    if ch.get("op") == "delete"), None)
    if del_pid:
        name = next((p.get("name") for p in pub_projects if p.get("id") == del_pid), None)
        result["impact"]["deletesProject"] = {
            "id": del_pid, "name": name or del_pid,
            "counts": await projects_repo.count_scope(del_pid)}
    return result


# ── Diff de campos por entidad (doc 31 — popup "Change details") ──────────


async def _detail_resolvers(wanted: list[tuple[str, str]], changes: dict,
                            before_by: dict[tuple[str, str], dict | None],
                            project_id: str) -> dict:
    """Mapas id→nombre para que el detalle sea legible: UDP, dominios, tablas/
    columnas referenciadas por relaciones y vistas, proyectos/folders. Slices
    puntuales — nunca colecciones completas de datos (solo estructura chica).

    `project_id` = el proyecto del changeset (doc 80 §8). Los catálogos de Data
    Standards son POR PROYECTO desde el doc 75; pedirlos sin él era `TypeError`
    (y `published("folders")` pelado, `MissingProjectError`) ⇒ 500 del popup."""
    from app.features.domains import repository as dom_repo
    from app.core.facets import udp_display_names
    from app.features.udp import repository as udp_repo

    cols = {c for c, _ in wanted}
    res: dict = {"udp": {}, "domains": {}, "tables": {}, "columns": {},
                 "projects": {}, "folders": {}}
    if cols & {"canonical_tables", "canonical_columns", "subject_areas"}:
        # Doc 69: la faceta lógica sale como «X (Logical)» (homónimos legibles).
        res["udp"] = udp_display_names(await udp_repo.list_udp(project_id))
    if "canonical_columns" in cols:
        res["domains"] = {d["id"]: d["name"] for d in await dom_repo.list_domains(project_id)}

    entries = []
    for c, e in wanted:
        ch = changes[c][e]
        before = ch.get("before") if ch.get("beforeAt") else before_by.get((c, e))
        entries.append((c, before, ch.get("payload")))
    tids, cids = diffdetail.collect_ref_ids(entries)

    def _merge_change_names(key: str, coll: str, ids: set[str]) -> None:
        # Entidad referida que también es NUEVA en este changeset: su nombre
        # solo existe en el payload del cambio (aún no publicada).
        for eid, ch in (changes.get(coll) or {}).items():
            if eid not in ids or eid in res[key]:
                continue
            p = ch.get("payload") or {}
            nm = p.get("physicalName") or p.get("logicalName") or p.get("name")
            if nm:
                res[key][eid] = nm

    if tids:
        docs = await repository.published("canonical_tables", {"_id": {"$in": sorted(tids)}})
        res["tables"] = {d["id"]: d.get("physicalName") or d.get("logicalName") or d["id"]
                         for d in docs}
        _merge_change_names("tables", "canonical_tables", tids)
    if cids:
        docs = await repository.published("canonical_columns", {"_id": {"$in": sorted(cids)}})
        res["columns"] = {d["id"]: d.get("physicalName") or d.get("logicalName") or d["id"]
                          for d in docs}
        _merge_change_names("columns", "canonical_columns", cids)
    if cols & {"folders", "subject_areas"}:
        # El changeset vive en UN proyecto: su nombre se resuelve por id, no
        # bajando el padrón entero (fuga de nombres entre proyectos).
        res["projects"] = {d["id"]: d.get("name") or d["id"]
                           for d in await repository.published("projects", {"_id": project_id})}
        _merge_change_names("projects", "projects", set(res["projects"]) | set(changes.get("projects") or {}))
    if "subject_areas" in cols:
        res["folders"] = {d["id"]: d.get("name") or d["id"]
                          for d in await repository.published("folders", scoped(project_id))}
        _merge_change_names("folders", "folders", set(res["folders"]) | set(changes.get("folders") or {}))
    return res


async def diff_details(cs_id: str, items: list[tuple[str, str]]) -> dict | None:
    """Diff de campos ANTES→DESPUÉS de entidades puntuales del changeset
    (doc 31). READ-ONLY y bajo demanda desde el popup de la revisión: no toca
    estado ni contrato de /diff. `before` = imagen histórica si el cambio la
    trae estampada (changeset aplicado), o el publicado VIVO en revisión;
    entidades que no pertenecen al changeset se OMITEN de la respuesta."""
    cs = await repository.get(cs_id)
    if not cs:
        return None
    wanted_cols = {c for c, _ in items}
    # Colecciones extra para resolver nombres de entidades referidas que también
    # cambian en este mismo changeset (tabla nueva + relación nueva, etc.).
    extra: set[str] = set()
    if wanted_cols & {"relationships", "views"}:
        extra |= {"canonical_tables", "canonical_columns"}
    if wanted_cols & {"folders", "subject_areas"}:
        extra |= {"projects", "folders"}
    changes = await repository.changes_map(cs_id, collections=sorted(wanted_cols | extra))

    seen: set[tuple[str, str]] = set()
    wanted: list[tuple[str, str]] = []
    for c, e in items:
        if (c, e) in seen:
            continue
        seen.add((c, e))
        if (changes.get(c) or {}).get(e):
            wanted.append((c, e))

    # `before` publicado vivo SOLO para cambios sin imagen estampada.
    before_by: dict[tuple[str, str], dict | None] = {}
    for col in {c for c, _ in wanted}:
        need = [e for c, e in wanted if c == col and not changes[col][e].get("beforeAt")]
        pubs = {d["id"]: d for d in await repository.published(col, {"_id": {"$in": need}})} \
            if need else {}
        for c, e in wanted:
            if c == col:
                before_by[(c, e)] = pubs.get(e)

    res = await _detail_resolvers(wanted, changes, before_by, cs["projectId"])
    return {"items": [diffdetail.entity_detail(c, e, changes[c][e], before_by[(c, e)], res)
                      for c, e in wanted]}


# ── Historial de auditoría por entidad (doc 51) ────────────────────────────


# Alcance del historial (pedido: tablas y columnas). Extender = agregar acá.
HISTORY_COLLECTIONS = ("canonical_tables", "canonical_columns", "views")


async def _history_resolvers(collection: str, project_id: str | None) -> dict:
    """Mapas id→nombre para el detalle de campos del historial: UDP siempre
    (ambas colecciones los llevan) y Parent Domain para columnas. Catálogos
    chicos de Data Standards — nunca colecciones de datos.

    `project_id` = el proyecto DE LA ENTIDAD (doc 80 §8). Sin él —entidad no
    publicada y cabeceras legacy sin proyecto— los mapas van vacíos: el
    historial se muestra igual, sólo sin resolver nombres de UDP/dominio.
    Llamarlos con `None` sería `MissingProjectError` ⇒ 500 de la pantalla."""
    from app.features.domains import repository as dom_repo
    from app.features.udp import repository as udp_repo

    res: dict = {"udp": {}, "domains": {}, "tables": {}, "columns": {},
                 "projects": {}, "folders": {}}
    if not project_id:
        return res
    res["udp"] = {d["id"]: d["name"] for d in await udp_repo.list_udp(project_id)}
    if collection == "canonical_columns":
        res["domains"] = {d["id"]: d["name"] for d in await dom_repo.list_domains(project_id)}
    return res


def _user_ref(uid: str | None, users: dict[str, dict]) -> dict | None:
    """Proyección {id, name, email} de un userId; un usuario borrado del Admin
    sale con el id crudo (el historial no pierde la fila)."""
    if not uid:
        return None
    u = users.get(uid)
    return {"id": uid, "name": (u or {}).get("name"), "email": (u or {}).get("email")}


async def entity_history(collection: str, entity_id: str, limit: int = 50) -> dict:
    """Historial PUBLICADO de una entidad (doc 51): deriva —en lectura— del
    ledger que el publish ya persiste (cambios con `before` estampado +
    cabeceras aplicadas). No escribe nada: no puede alterar versionamiento,
    merge ni publicaciones concurrentes.

    Si la entidad no nació por changeset (migración Erwin: escribe directo y
    el marcador v1 es su versión) o su primer evento es una edición, se
    agrega al final una entrada SINTÉTICA "Initial load": fecha = `createdAt`
    del doc publicado (el loader la estampa) o `appliedAt` del marcador."""
    changes = await repository.entity_changes(collection, entity_id)
    headers = await repository.changesets_by_ids(
        [str(c.get("csId") or "") for c in changes])
    events = history_events(changes, headers)[: max(1, limit)]

    # Proyecto de la entidad: manda la cabecera de sus versiones y, si son
    # legacy (pre-doc-75, sin `projectId`), el doc publicado. Se lee UNA vez y
    # se reusa para el marcador «Initial load» de más abajo.
    pub = await repository.published(collection, {"_id": entity_id})
    doc = next((d for d in pub if str(d.get("id") or "") == entity_id), None)
    pid = next((h.get("projectId") for h in headers.values() if h.get("projectId")), None) \
        or (doc or {}).get("projectId")

    res = await _history_resolvers(collection, pid) if events else {}
    if events and collection == "views":
        # Doc 61: las filas "Columns · <tabla>" del detalle resuelven la tabla
        # fuente por NOMBRE (mismo criterio que _detail_resolvers).
        entries = [("views", e["change"].get("before"), e["change"].get("payload"))
                   for e in events]
        tids, _cids = diffdetail.collect_ref_ids(entries)
        if tids:
            docs = await repository.published(
                "canonical_tables", {"_id": {"$in": sorted(tids)}})
            res["tables"] = {d["id"]: d.get("physicalName") or d.get("logicalName")
                             or d["id"] for d in docs}
    users: dict[str, dict] = {}
    ids = {e["userId"] for e in events} | {e["publishedById"] for e in events} \
        | {uid for e in events for uid in e["approvedByIds"]}
    if ids - {None}:
        from app.features.auth import repository as auth_repo
        users = {u["id"]: u for u in await auth_repo.list_users()}

    items: list[dict] = []
    for e in events:
        detail = diffdetail.entity_detail(collection, entity_id, e["change"], None, res)
        items.append({
            "action": e["action"], "at": e["at"], "editedAt": e["editedAt"],
            "csId": e["csId"], "version": e["version"], "versionTitle": e["versionTitle"],
            "user": _user_ref(e["userId"], users),
            "publishedBy": _user_ref(e["publishedById"], users),
            "approvedBy": [u for uid in e["approvedByIds"] if (u := _user_ref(uid, users))],
            "origin": e["origin"], "name": detail.get("name"),
            "restoredFrom": e.get("restoredFrom"),
            "fields": detail.get("fields") or [], "synthetic": False,
        })

    if not any(i["action"] == "created" for i in items):
        if items or doc:
            # Doc 75: el marcador «Initial load» es el v1 DEL PROYECTO de la entidad.
            first = await repository.earliest_applied(pid) if pid else None
            if first:
                items.append({
                    "action": "created", "at": (doc or {}).get("createdAt") or first.get("appliedAt"),
                    "editedAt": None, "csId": first.get("id"),
                    "version": first.get("versionLabel"), "versionTitle": first.get("title"),
                    "user": None, "publishedBy": None, "approvedBy": [],
                    "origin": {"kind": "migration"},
                    "name": None, "fields": [], "synthetic": True,
                })
    return {"items": items}


# ── Comparación entre versiones publicadas (doc 65) ────────────────────────


def compose_versions_range(ordered_changes: list[dict],
                           headers: list[dict] | None = None) -> tuple[dict, list[dict]]:
    """Compone los ledgers de versiones aplicadas EN ORDEN CRONOLÓGICO
    (oldest→newest) en un estado NETO por entidad. Puro.

    Por entidad tocada en el rango: `before` = imagen previa de la PRIMERA
    versión que la tocó (el estado tal como quedó en la versión base del
    compare) y `op`/`after` = el desenlace de la ÚLTIMA. Devuelve
    ({(collection, entityId): {before, op, after}}, faltantes) — `faltantes` =
    entidades cuya primera versión del rango no estampó imagen previa
    (publicadas antes de la captura de before-images): su "antes" real es
    irreconstruible y se EXCLUYEN del diff (nunca se inventa un antes)."""
    state: dict[tuple[str, str], dict] = {}
    for idx, changes in enumerate(ordered_changes):
        # Doc 84 C2: cabecera de la versión (paralela a `ordered_changes`) para
        # atribuir cada entidad a la ÚLTIMA versión del rango que la tocó.
        hdr = (headers[idx] if headers and idx < len(headers) else None) or None
        for coll, per in (changes or {}).items():
            for eid, ch in per.items():
                entry = state.get((coll, eid))
                if entry is None:
                    entry = {"missing": not ch.get("beforeAt"),
                             "before": ch.get("before") if ch.get("beforeAt") else None}
                    state[(coll, eid)] = entry
                entry["op"] = ch.get("op")
                entry["after"] = ch.get("payload") if ch.get("op") != "delete" else None
                if hdr:
                    entry["lastVersion"] = {"id": hdr.get("id"), "versionLabel": hdr.get("versionLabel"),
                                            "owner": hdr.get("owner"), "appliedAt": hdr.get("appliedAt")}
    out: dict[tuple[str, str], dict] = {}
    missing: list[dict] = []
    for (coll, eid), entry in state.items():
        if entry.pop("missing", False):
            doc = entry.get("after") or {}
            missing.append({"collection": coll, "id": eid,
                            "name": doc.get("physicalName") or doc.get("logicalName")
                            or doc.get("name")})
            continue
        out[(coll, eid)] = entry
    return out, missing


def compare_buckets(composed: dict, res: dict) -> dict:
    """Estado neto compuesto → buckets {collection: {added, edited, deleted}}
    con nombres legibles (relaciones como "PADRE → HIJO" vía resolvers). El
    verbo exige DIFERENCIA VISIBLE con el mismo motor de campos del popup de
    revisión: una entidad editada y luego devuelta a su estado original dentro
    del rango no es un cambio (neto cero), igual que una creada y borrada. Puro."""
    cols: dict[str, dict] = {}
    for (coll, eid), entry in composed.items():
        before, op = entry.get("before"), entry.get("op")
        detail = diffdetail.entity_detail(
            coll, eid, {"op": op, "payload": entry.get("after"),
                        "before": before, "beforeAt": "composed"}, None, res)
        if op == "delete":
            if before is None:
                continue  # creada y borrada dentro del rango: neto cero
            verb = "deleted"
        elif before is None:
            verb = "added"
        elif not detail["fields"]:
            continue  # sin campos visibles distintos: neto cero
        else:
            verb = "edited"
        bucket = cols.setdefault(coll, {"added": [], "edited": [], "deleted": []})
        bucket[verb].append({"id": eid, "name": detail["name"], "collection": coll,
                             "lastVersion": entry.get("lastVersion")})
    for bucket in cols.values():
        for verb in ("added", "edited", "deleted"):
            bucket[verb].sort(key=lambda e: str(e.get("name") or "").lower())
    return cols


async def _compare_resolvers(composed: dict, project_id: str) -> dict:
    """Mapas id→nombre para el compare (doc 65) — mismos catálogos que el popup
    de revisión (UDP, dominios, tablas/columnas referidas, proyectos/folders),
    con fallback a los DOCS DEL PROPIO RANGO para entidades que ya no existen
    publicadas (p.ej. una tabla referida por una relación y borrada después).

    `project_id` = el proyecto del rango comparado (doc 80 §8): las dos
    versiones son del mismo proyecto por construcción de `_compare_headers`."""
    from app.features.domains import repository as dom_repo
    from app.features.udp import repository as udp_repo

    cols = {c for c, _ in composed}
    res: dict = {"udp": {}, "domains": {}, "tables": {}, "columns": {},
                 "projects": {}, "folders": {}}
    if cols & {"canonical_tables", "canonical_columns", "subject_areas"}:
        res["udp"] = {d["id"]: d["name"] for d in await udp_repo.list_udp(project_id)}
    if "canonical_columns" in cols:
        res["domains"] = {d["id"]: d["name"] for d in await dom_repo.list_domains(project_id)}
    entries = [(coll, entry.get("before"), entry.get("after"))
               for (coll, _eid), entry in composed.items()]
    tids, cids = diffdetail.collect_ref_ids(entries)

    def _range_names(key: str, coll: str, ids: set[str]) -> None:
        # Nombre desde los docs del rango cuando el publicado ya no lo tiene.
        for (c, eid), entry in composed.items():
            if c != coll or eid not in ids or eid in res[key]:
                continue
            doc = entry.get("after") or entry.get("before") or {}
            nm = doc.get("physicalName") or doc.get("logicalName") or doc.get("name")
            if nm:
                res[key][eid] = nm

    if tids:
        docs = await repository.published("canonical_tables", {"_id": {"$in": sorted(tids)}})
        res["tables"] = {d["id"]: d.get("physicalName") or d.get("logicalName") or d["id"]
                         for d in docs}
        _range_names("tables", "canonical_tables", tids)
    if cids:
        docs = await repository.published("canonical_columns", {"_id": {"$in": sorted(cids)}})
        res["columns"] = {d["id"]: d.get("physicalName") or d.get("logicalName") or d["id"]
                          for d in docs}
        _range_names("columns", "canonical_columns", cids)
    if cols & {"folders", "subject_areas"}:
        res["projects"] = {d["id"]: d.get("name") or d["id"]
                           for d in await repository.published("projects", {"_id": project_id})}
        _range_names("projects", "projects", {e for (c, e) in composed if c == "projects"})
    if "subject_areas" in cols:
        res["folders"] = {d["id"]: d.get("name") or d["id"]
                          for d in await repository.published("folders", scoped(project_id))}
        _range_names("folders", "folders", {e for (c, e) in composed if c == "folders"})
    return res


async def _compare_headers(from_id: str, to_id: str):
    """Valida y NORMALIZA los extremos del compare al orden cronológico.
    Devuelve (older, newer) o sentinel: None (no existe) · "same" (misma
    versión en ambos extremos) · "not-applied" (una no está publicada)."""
    a, b = await repository.get(from_id), await repository.get(to_id)
    if not a or not b:
        return None
    if a["id"] == b["id"]:
        return "same"
    for cs in (a, b):
        if cs.get("status") != "approved" or not cs.get("appliedAt"):
            return "not-applied"
    if a.get("projectId") != b.get("projectId"):
        return "different-projects"          # doc 75 I6: nunca se comparan proyectos distintos
    if str(a["appliedAt"]) > str(b["appliedAt"]):
        a, b = b, a
    return a, b


async def _compare_composed(from_id: str, to_id: str):
    """Rango aplicado (older, newer] COMPUESTO: (older, newer, span, composed,
    faltantes) o el sentinel de `_compare_headers`. `span` = cabeceras de las
    versiones del rango en orden cronológico (newer incluida)."""
    norm = await _compare_headers(from_id, to_id)
    if norm is None or isinstance(norm, str):
        return norm
    older, newer = norm
    span = [v for v in await repository.applied_after(older["projectId"], older["appliedAt"])
            if str(v.get("appliedAt") or "") <= str(newer["appliedAt"])]
    span.sort(key=lambda v: str(v.get("appliedAt") or ""))
    if not any(v["id"] == newer["id"] for v in span):
        # appliedAt idénticos entre versiones distintas (edge legacy): el
        # extremo nuevo entra igual al rango.
        span.append({"id": newer["id"], "appliedAt": newer.get("appliedAt"),
                     "versionLabel": newer.get("versionLabel")})
    composed, missing = compose_versions_range(
        [await repository.changes_map(v["id"]) for v in span], headers=span)
    return older, newer, span, composed, missing


async def compare_versions(from_id: str, to_id: str) -> dict | str | None:
    """Diferencias NETAS entre dos versiones publicadas (doc 65): qué entidades
    quedaron distintas entre el estado de `from` y el de `to`, con conteos por
    colección. READ-ONLY: deriva del mismo ledger que alimenta historial y
    rollback — no toca estado ni contratos existentes. `fromId`/`toId` aceptan
    cualquier orden (se normaliza a cronológico)."""
    got = await _compare_composed(from_id, to_id)
    if got is None or isinstance(got, str):
        return got
    older, newer, span, composed, missing = got

    def _hdr(cs: dict) -> dict:
        return {"id": cs["id"], "versionLabel": cs.get("versionLabel"),
                "title": cs.get("title"), "appliedAt": cs.get("appliedAt")}

    buckets = compare_buckets(composed, await _compare_resolvers(composed, older["projectId"]))
    counts = {"added": 0, "edited": 0, "deleted": 0}
    for bucket in buckets.values():
        for verb in counts:
            counts[verb] += len(bucket[verb])
    tree = await _compare_tree(buckets, composed, older["projectId"])
    return {"from": _hdr(older), "to": _hdr(newer), "versionsSpanned": len(span),
            "collections": buckets,
            "counts": {**counts, "total": sum(counts.values())},
            "unreconstructed": missing,
            "tree": tree["tree"], "orphans": tree["orphans"], "structure": tree["structure"]}


async def _compare_tree(buckets: dict, composed: dict, project_id: str) -> dict:
    """Doc 84 C1: la MISMA jerarquía del popup de revisión para el compare —
    `build_diff_tree` sobre el estado neto compuesto. Meta de tablas: los docs
    del rango (payload, o imagen previa para los deletes) + las publicadas que
    sólo aparecen como dueñas de columnas cambiadas. Canvases/carpetas/
    proyecto: publicados VIVOS (la membresía de HOY ubica cada tabla; una tabla
    ya fuera de todo canvas cae en «Not on any canvas»)."""
    changes: dict[str, dict] = {}
    published: dict[str, list[dict]] = {"canonical_tables": [], "canonical_columns": [], "views": []}
    for (coll, eid), entry in composed.items():
        changes.setdefault(coll, {})[eid] = {"op": entry.get("op"), "payload": entry.get("after")}
        if coll in published and entry.get("op") == "delete" and entry.get("before"):
            published[coll].append({**entry["before"], "id": eid})
    owners = {(e.get("after") or e.get("before") or {}).get("tableId")
              for (c, _eid), e in composed.items() if c == "canonical_columns"}
    owners = {t for t in owners if t} - {eid for (c, eid) in composed if c == "canonical_tables"}
    if owners:
        published["canonical_tables"] = published["canonical_tables"] + await repository.published(
            "canonical_tables", {"_id": {"$in": sorted(owners)}})
    sas = await repository.published("subject_areas", scoped(project_id))
    projs = await repository.published("projects", {"_id": project_id})
    flds = await repository.published("folders", scoped(project_id))
    return build_diff_tree(buckets, changes, published, sas, projs, flds)


async def compare_details(from_id: str, to_id: str,
                          items: list[tuple[str, str]]) -> dict | str | None:
    """Detalle de campos ANTES→DESPUÉS de entidades PUNTUALES del rango
    comparado (doc 65) — mismo shape del popup doc 31 (el front reutiliza el
    renderer). Bajo demanda por selección; entidades fuera del rango se OMITEN
    de la respuesta (igual que diff_details con las ajenas al changeset)."""
    got = await _compare_composed(from_id, to_id)
    if got is None or isinstance(got, str):
        return got
    older, _newer, _span, composed, _missing = got
    seen: set[tuple[str, str]] = set()
    wanted: list[tuple[str, str]] = []
    for c, e in items:
        if (c, e) in seen:
            continue
        seen.add((c, e))
        if (c, e) in composed:
            wanted.append((c, e))
    res = await _compare_resolvers(composed, older["projectId"])
    out = []
    for c, e in wanted:
        entry = composed[(c, e)]
        out.append(diffdetail.entity_detail(
            c, e, {"op": entry.get("op"), "payload": entry.get("after"),
                   "before": entry.get("before"), "beforeAt": "composed"}, None, res))
    return {"items": out}


# Los compat M-series /approve y /reject ya NO tienen funciones propias: el
# router delega en `review()` (misma política de unanimidad + is_assigned).
# Antes `approve()` aplicaba con UNA sola aprobación, salteando la unanimidad.
