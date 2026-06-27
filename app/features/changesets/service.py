"""Negocio de `changesets`: registrar cambios, overlay, transiciones,
versiones/requests y diff enriquecido.

La política de aprobación vive en funciones **puras** (testables sin DB):
`next_version_label`, `record_approval`, `approval_outcome`, `is_assigned`,
`structured_diff`. Las funciones `async` sólo orquestan repository + estas puras.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from app.core.versioning import overlay, summarize_diff

from . import repository
from .repository import VERSIONED


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Acumulación de cambios (M-series) ─────────────────────────────────────


def record_change(changes: dict, collection: str, entity_id: str, op: str, payload: dict | None) -> dict:
    """Acumula un cambio (upsert/delete) en el dict `changes` del changeset. Puro."""
    changes = {k: dict(v) for k, v in changes.items()}
    col = changes.setdefault(collection, {})
    col[entity_id] = {"op": op} if op == "delete" else {"op": "upsert", "payload": payload or {}}
    return changes


def apply_plan(changes: dict) -> list[tuple]:
    """Linealiza `changes` en (collection, entityId, op, payload|None)."""
    plan: list[tuple] = []
    for collection, col_changes in changes.items():
        for eid, ch in col_changes.items():
            payload = ch.get("payload") if ch.get("op") != "delete" else None
            plan.append((collection, eid, ch.get("op"), payload))
    return plan


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
) -> dict:
    """Diff por colección con nombres + impacto. Puro.

    - `changes`: dict completo del changeset {collection: {eid: {op, payload?}}}.
    - `published`: {collection: [doc,...]} estado publicado de cada colección.
    - `relationships`: relaciones publicadas (para afectación a otras tablas).

    Devuelve `{collections: {col: {added,edited,deleted}}, impact: {...}}` donde
    cada item es `{id, name, collection}`. El impacto cuenta tablas/columnas
    tocadas y las OTRAS tablas afectadas vía relationships con las tocadas.
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
                payload = (col_changes.get(eid) or {}).get("payload")
                buckets[bucket_of[kind]].append(
                    {"id": eid, "name": _entity_name(meta, eid, payload), "collection": col}
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
        src, tgt = rel.get("sourceTableId"), rel.get("targetTableId")
        if src in touched_table_ids and tgt and tgt not in touched_table_ids:
            affected.add(tgt)
        if tgt in touched_table_ids and src and src not in touched_table_ids:
            affected.add(src)
    return [{"id": tid, "name": names.get(tid)} for tid in sorted(affected)]


def version_row(cs: dict) -> dict:
    """Proyección de un changeset como fila de la tabla de versiones (p5)."""
    return {
        "id": cs["id"],
        "versionLabel": cs.get("versionLabel"),
        "title": cs.get("title"),
        "owner": cs.get("owner"),
        "status": cs.get("status"),
        "projectIds": cs.get("projectIds", []),
        "reviewers": cs.get("reviewers", []),
        "createdAt": cs.get("createdAt"),
        "updatedAt": cs.get("updatedAt"),
        "submittedAt": cs.get("submittedAt"),
    }


# ── Orquestación async (repository + puras) ───────────────────────────────


async def create(title: str, owner: str) -> dict:
    return await repository.create(title, owner)


async def get(cs_id: str) -> dict | None:
    cs = await repository.get(cs_id)
    if not cs:
        return None
    # Adjunta un resumen de diff por colección para el aprobador (compat M-series).
    diff = {}
    for col in VERSIONED:
        col_changes = cs["changes"].get(col)
        if col_changes:
            diff[col] = summarize_diff(await repository.published(col), col_changes)
    return {**cs, "diff": diff}


async def list_all() -> list[dict]:
    return await repository.list_all()


async def list_versions() -> list[dict]:
    """Lista cross-project como filas de versión (sin el `changes` crudo)."""
    return [version_row(cs) for cs in await repository.list_all()]


async def current_production() -> dict | None:
    """Última versión aplicada/approved (fila de producción verde en el UI)."""
    approved = [cs for cs in await repository.list_all() if cs.get("status") == "approved"]
    if not approved:
        return None
    approved.sort(key=lambda c: c.get("reviewedAt") or c.get("updatedAt") or "", reverse=True)
    return version_row(approved[0])


async def list_requests(reviewer: str | None = None, owner: str | None = None) -> list[dict]:
    """Publish requests en revisión (`submitted`). Filtra por revisor asignado
    (`reviewer`, p.ej. el actor) y/o por solicitante (`owner`)."""
    out = []
    for cs in await repository.list_all():
        if cs.get("status") != "submitted":
            continue
        if reviewer is not None and reviewer not in cs.get("reviewers", []):
            continue
        if owner is not None and cs.get("owner") != owner:
            continue
        out.append(version_row(cs))
    return out


async def snapshot(actor: str, title: str | None, description: str | None,
                   project_ids: list[str], version_label: str | None) -> dict:
    """Crea un draft (working copy) a partir del estado publicado.
    `versionLabel` autoincremental si no se pasa; `owner = actor`."""
    if not version_label:
        existing = [cs.get("versionLabel") for cs in await repository.list_all()]
        version_label = next_version_label(existing)
    fields = {
        "description": description,
        "versionLabel": version_label,
        "projectIds": project_ids or [],
    }
    return await repository.create(title or version_label, actor, extra=fields)


async def add_change(cs_id: str, collection: str, entity_id: str, op: str, payload: dict | None) -> dict | None:
    cs = await repository.get(cs_id)
    if not cs or cs["status"] != "draft":
        return None
    changes = record_change(cs["changes"], collection, entity_id, op, payload)
    return await repository.set_changes(cs_id, changes)


async def effective(cs_id: str, collection: str) -> list[dict] | None:
    cs = await repository.get(cs_id)
    if not cs:
        return None
    return overlay(await repository.published(collection), cs["changes"].get(collection, {}))


async def submit(cs_id: str, title: str | None = None, description: str | None = None,
                 reviewers: list[str] | None = None, project_ids: list[str] | None = None) -> dict | None:
    """Crea/extiende el publish request: guarda revisores/descripción y pasa a
    `submitted`. Si ya estaba submitted, sólo extiende (re-asignar revisores)."""
    cs = await repository.get(cs_id)
    if not cs or cs["status"] not in ("draft", "submitted"):
        return None
    fields: dict = {"status": "submitted", "submittedAt": cs.get("submittedAt") or _now()}
    if title is not None:
        fields["title"] = title
    if description is not None:
        fields["description"] = description
    if reviewers is not None:
        fields["reviewers"] = reviewers
    if project_ids is not None:
        fields["projectIds"] = project_ids
    return await repository.set_status(cs_id, fields)


async def _apply_and_finalize(cs: dict, fields: dict) -> dict | None:
    """Aplica el changeset a las colecciones publicadas + cascada de dominios
    y persiste los `fields` de cierre (status='approved', …)."""
    await repository.apply_changes(apply_plan(cs["changes"]))
    if cs["changes"].get("parent_domains"):
        await repository.cascade_domain_types(cs["changes"]["parent_domains"])
    return await repository.set_status(cs["id"], fields)


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

    approvals = record_approval(cs.get("approvals", {}), actor, decision, note)
    outcome = approval_outcome(cs.get("reviewers", []), approvals)

    if outcome == "approved":
        return await _apply_and_finalize(
            cs, {"approvals": approvals, "status": "approved",
                 "reviewedBy": actor, "reviewedAt": _now()}
        )
    if outcome == "rejected":
        return await repository.set_status(
            cs_id, {"approvals": approvals, "status": "rejected",
                    "reviewedBy": actor, "reviewedAt": _now(), "reviewNote": note}
        )
    # Aún faltan aprobaciones: guarda la decisión, sigue en revisión.
    return await repository.set_status(cs_id, {"approvals": approvals})


async def add_comment(cs_id: str, actor: str, text: str) -> dict | None:
    """Agrega `{author, text, at}` al hilo de comentarios del changeset."""
    cs = await repository.get(cs_id)
    if not cs:
        return None
    comments = list(cs.get("comments", [])) + [{"author": actor, "text": text, "at": _now()}]
    return await repository.set_status(cs_id, {"comments": comments})


async def diff(cs_id: str) -> dict | None:
    """Diff estructurado por colección (added/edited/deleted con nombres) + impacto."""
    cs = await repository.get(cs_id)
    if not cs:
        return None
    published = {col: await repository.published(col) for col in VERSIONED}
    relationships = await repository.published("relationships")
    return structured_diff(cs["changes"], published, relationships)


# ── Transiciones directas (compat M-series: approve/reject sin reviewers) ──


async def reject(cs_id: str, reviewer: str, note: str | None) -> dict | None:
    cs = await repository.get(cs_id)
    if not cs or cs["status"] != "submitted":
        return None
    return await repository.set_status(
        cs_id, {"status": "rejected", "reviewedBy": reviewer, "reviewedAt": _now(), "reviewNote": note}
    )


async def approve(cs_id: str, reviewer: str) -> dict | None:
    cs = await repository.get(cs_id)
    if not cs or cs["status"] != "submitted":
        return None
    return await _apply_and_finalize(
        cs, {"status": "approved", "reviewedBy": reviewer, "reviewedAt": _now()}
    )
