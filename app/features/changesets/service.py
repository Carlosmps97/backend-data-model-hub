"""Negocio de `changesets`: registrar cambios, overlay, transiciones,
versiones/requests y diff enriquecido.

La política de aprobación vive en funciones **puras** (testables sin DB):
`next_version_label`, `record_approval`, `approval_outcome`, `is_assigned`,
`structured_diff`. Las funciones `async` sólo orquestan repository + estas puras.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from app.core.logging import get_logger
from app.core.versioning import overlay, summarize_diff

from . import repository, validation
from .repository import VERSIONED
from .validation import DuplicateEntityError, InvalidPayloadError

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
        "appliedAt": cs.get("appliedAt"),
    }


# ── Orquestación async (repository + puras) ───────────────────────────────


async def create(title: str, owner: str) -> dict:
    return await repository.create(title, owner)


async def get(cs_id: str) -> dict | None:
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


async def list_versions() -> list[dict]:
    """Lista cross-project como filas de versión (sin el `changes` crudo)."""
    return [version_row(cs) for cs in await repository.list_summaries()]


async def current_production() -> dict | None:
    """Última versión APLICADA (fila de producción verde en el UI). Prefiere
    las approved con `appliedAt` (apply completo confirmado): una `approved`
    sin appliedAt es un publish interrumpido a mitad — no está en producción.
    Fallback a approved a secas para docs legacy previos al marcador."""
    approved = [cs for cs in await repository.list_summaries() if cs.get("status") == "approved"]
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
        out.append(version_row(cs))
    return out


async def snapshot(actor: str, title: str | None, description: str | None,
                   project_ids: list[str], version_label: str | None) -> dict:
    """Crea un draft (working copy) a partir del estado publicado.
    `versionLabel` autoincremental si no se pasa; `owner = actor`."""
    if not version_label:
        existing = [cs.get("versionLabel") for cs in await repository.list_summaries()]
        version_label = next_version_label(existing)
    fields = {
        "description": description,
        "versionLabel": version_label,
        "projectIds": project_ids or [],
    }
    return await repository.create(title or version_label, actor, extra=fields)


def _table_dup_filter(payload: dict) -> dict:
    """Filtro Mongo del chequeo de unicidad de tabla: (schema, physicalName)
    por regex anclado case-insensitive — usa el índice compuesto
    schema+physicalName de canonical_tables. Sin schema matchea null/''. Puro."""
    name = str(payload.get("physicalName") or "").strip()
    schema = str(payload.get("schema") or payload.get("sql_schema") or "").strip()
    return {
        "schema": ({"$regex": f"^{re.escape(schema)}$", "$options": "i"}
                   if schema else {"$in": [None, ""]}),
        "physicalName": {"$regex": f"^{re.escape(name)}$", "$options": "i"},
    }


async def _duplicate_error(cs_id: str, collection: str, entity_id: str,
                           op: str, payload: dict | None) -> str | None:
    """Chequeo de unicidad de un upsert (spec 10 §9) contra el publicado
    ACTIVO (slice indexado: tablas por schema+physicalName, columnas por
    tableId) + los upserts PENDIENTES del mismo changeset. None si pasa o no
    aplica (deletes / otras colecciones)."""
    if op == "delete" or collection not in ("canonical_tables", "canonical_columns"):
        return None
    p = payload or {}
    if not str(p.get("physicalName") or "").strip():
        return None
    if collection == "canonical_tables":
        pub = await repository.published(collection, _table_dup_filter(p))
    else:
        if not p.get("tableId"):
            return None
        pub = await repository.published(collection, {"tableId": p["tableId"]})
    pending = (await repository.changes_map(cs_id, [collection])).get(collection, {})
    return validation.duplicate_error(collection, entity_id, p, pub, pending)


async def add_change(cs_id: str, actor: str, collection: str, entity_id: str, op: str, payload: dict | None) -> dict | str | None:
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
    err = validation.payload_error(collection, entity_id, op, payload)
    if err:
        raise InvalidPayloadError(err)
    # Unicidad de nombres (spec 10 §9): tablas por (schema, physicalName) y
    # columnas por physicalName dentro de su tableId — contra publicado activo
    # + pendientes de ESTE changeset. El router lo convierte en 409.
    dup = await _duplicate_error(cs_id, collection, entity_id, op, payload)
    if dup:
        raise DuplicateEntityError(dup)
    updated = await repository.set_change(cs_id, collection, entity_id, op, payload)
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
                    q: str | None = None, limit: int | None = None) -> list[dict] | None:
    """Estado efectivo (publicado + overlay del changeset) de una colección.

    `table_id`/`ids` acotan la lectura a un SLICE: sin filtro, un GET de
    canonical_columns con 300k documentos publicados mueve toda la colección
    por request. Con filtro, los `changes` del changeset también se acotan al
    slice (una columna nueva de OTRA tabla no debe colarse en la respuesta).

    `q`/`limit` (excluyentes con los slices): búsqueda server-side por nombre
    para los modales de catálogo — publicado filtrado+capado en Mongo, cambios
    del changeset filtrados por el MISMO criterio, y re-filtro post-overlay
    (un upsert puede renombrar la entidad y sacarla del match)."""
    cs = await repository.get(cs_id)
    if not cs:
        return None
    changes = (await repository.changes_map(cs_id, [collection])).get(collection, {})

    if q is not None or limit is not None:
        # `limit` sin `q` = página inicial del modal (primeras N por nombre).
        # sort en Mongo SOLO donde hay índice (canonical_tables.physicalName):
        # Cosmos RU rechaza .sort() sobre campos sin índice.
        q = q or ""
        sort_field = "physicalName" if collection == "canonical_tables" else None
        pub = await repository.published(collection, _q_filter(q) if q else None,
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
        flt = ({"$or": [{"sourceTableId": table_id}, {"targetTableId": table_id}]}
               if collection == "relationships" else {"tableId": table_id})
    pub = await repository.published(collection, flt)
    if flt is not None:
        in_slice = {d["id"] for d in pub}
        wanted = set(ids or [])

        def _in_scope(ch: dict) -> bool:
            p = ch.get("payload") or {}
            if table_id is None:
                return False
            if collection == "relationships":
                return table_id in (p.get("sourceTableId"), p.get("targetTableId"))
            return p.get("tableId") == table_id

        changes = {
            eid: ch for eid, ch in changes.items()
            if eid in in_slice or eid in wanted or _in_scope(ch)
        }
    return overlay(pub, changes)


async def submit(cs_id: str, actor: str, title: str | None = None, description: str | None = None,
                 reviewers: list[str] | None = None, project_ids: list[str] | None = None) -> dict | str | None:
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
    fields: dict = {"status": "submitted", "submittedAt": _now(), "approvals": {}}
    if title is not None:
        fields["title"] = title
    if description is not None:
        fields["description"] = description
    if reviewers is not None:
        fields["reviewers"] = reviewers
    if project_ids is not None:
        fields["projectIds"] = project_ids
    return await repository.transition(cs_id, "draft", fields)


async def _apply_and_finalize(cs_id: str, fields: dict, submitted_at: str | None) -> dict | None:
    """Cierra el request y aplica el changeset a las colecciones publicadas
    (+ cascada de dominios). Orden y garantías:

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
    4. Si el apply/cascada FALLA (throttling de Cosmos, timeout), devuelve el
       request a `submitted` y re-lanza: el apply es idempotente (upserts por
       _id), re-aprobar reintenta y converge. Si el PROCESO muere a mitad, el
       doc queda `approved` sin `appliedAt` — detectable, y recuperable con
       `scripts/reapply_changeset.py`.
    5. `appliedAt` se estampa recién con el apply completo: es el marcador de
       "esta versión SÍ está en producción" (lo usa current_production).
    """
    final = await repository.transition(
        cs_id, "submitted", fields, expect={"submittedAt": submitted_at}
    )
    if final is None:
        return None
    changes = changes_in_cycle(await repository.changes_map(cs_id), submitted_at)
    _revert = {"status": "submitted", "reviewedBy": None, "reviewedAt": None, "approvals": {}}
    invalid = validation.validate_changes(changes)
    if invalid:
        await repository.transition(cs_id, fields.get("status", "approved"), _revert)
        raise InvalidPayloadError(
            "el request contiene cambios inválidos y no se puede publicar — "
            + " | ".join(invalid[:5])
        )
    try:
        counts = await repository.apply_changes(apply_plan(changes))
        if changes.get("parent_domains"):
            await repository.cascade_domain_types(changes["parent_domains"])
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
            cs_id, {"status": "approved", "reviewedBy": actor, "reviewedAt": _now()},
            submitted_at=updated.get("submittedAt"),
        )
    if outcome == "rejected":
        # Guard atómico también en el cierre negativo: un withdraw concurrente
        # (submitted→draft) no debe quedar pisado por un 'rejected' tardío, y
        # el expect por submittedAt evita rechazar un RE-envío que el revisor
        # nunca vio (ABA withdraw→resubmit).
        return await repository.transition(
            cs_id, "submitted",
            {"status": "rejected", "reviewedBy": actor, "reviewedAt": _now(), "reviewNote": note},
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
        cs_id, "submitted", {"status": "draft", "approvals": {}, "submittedAt": None}
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
    return await repository.transition(
        cs_id, "rejected",
        {"status": "draft", "approvals": {}, "submittedAt": None,
         "reviewedBy": None, "reviewedAt": None, "reviewNote": None},
    )


async def add_comment(cs_id: str, actor: str, text: str) -> dict | None:
    """Agrega `{author, text, at}` al hilo con `$push` atómico (comentarios
    concurrentes no se pisan)."""
    return await repository.push_comment(cs_id, {"author": actor, "text": text, "at": _now()})


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
        relationships = await repository.published(
            "relationships", {"$or": [{"sourceTableId": {"$in": t}}, {"targetTableId": {"$in": t}}]}
        )

    # Nombres de las tablas AFECTADAS vía relaciones (fuera del slice de tocadas).
    known = {d["id"] for d in published["canonical_tables"]}
    rel_tids = {r.get("sourceTableId") for r in relationships} | {r.get("targetTableId") for r in relationships}
    missing = [tid for tid in rel_tids if tid and tid not in known]
    if missing:
        published["canonical_tables"] = published["canonical_tables"] + await repository.published(
            "canonical_tables", {"_id": {"$in": missing}}
        )

    return structured_diff(changes, published, relationships, baseline=cs.get("createdAt"))


# Los compat M-series /approve y /reject ya NO tienen funciones propias: el
# router delega en `review()` (misma política de unanimidad + is_assigned).
# Antes `approve()` aplicaba con UNA sola aprobación, salteando la unanimidad.
