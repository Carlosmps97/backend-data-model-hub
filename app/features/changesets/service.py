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
from app.features.relationships.models import RelationshipDoc
from app.features.schemas import service as schemas_service
from app.features.settings import service as settings_service
from app.features.views.models import normalize_source_tables

from . import diffdetail, repository, validation
from .repository import VERSIONED
from .validation import (
    DuplicateEntityError, InvalidPayloadError, NameTooLongError,
    RelationshipKeyMismatchError, SchemaInUseError, relationship_key_error)

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
        parent = rel.get("parentTableId") or rel.get("targetTableId")
        child = rel.get("childTableId") or rel.get("sourceTableId")
        if parent in touched_table_ids and child and child not in touched_table_ids:
            affected.add(child)
        if child in touched_table_ids and parent and parent not in touched_table_ids:
            affected.add(parent)
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
    """Chequeo de unicidad de un upsert (spec 10 §9 + doc 18) contra el
    publicado ACTIVO (slice indexado: tablas por schema+physicalName, columnas
    por tableId, esquemas por name) + los upserts PENDIENTES del mismo
    changeset. None si pasa o no aplica (deletes / otras colecciones)."""
    if op == "delete" or collection not in ("canonical_tables", "canonical_columns", "schemas"):
        return None
    p = payload or {}
    if collection == "schemas":
        name = str(p.get("name") or "").strip()
        if not name:
            return None
        pub = await repository.published(
            collection, {"name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}})
    elif not str(p.get("physicalName") or "").strip():
        return None
    elif collection == "canonical_tables":
        pub = await repository.published(collection, _table_dup_filter(p))
    else:
        if not p.get("tableId"):
            return None
        pub = await repository.published(collection, {"tableId": p["tableId"]})
    pending = (await repository.changes_map(cs_id, [collection])).get(collection, {})
    return validation.duplicate_error(collection, entity_id, p, pub, pending)


async def _name_length_error(cs_id: str, collection: str, entity_id: str,
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
    max_len = int((await settings_service.get_naming_for(scope)).get("maxLength") or 0)
    if not max_len or len(name) <= max_len:
        return None  # dentro del límite (o límite en 0 = desactivado)
    # Excede: bloquear SOLO si es nuevo o el físico CAMBIÓ respecto del estado
    # efectivo (pendiente del changeset o publicado).
    pend = (await repository.changes_map(cs_id, [collection])).get(collection, {})
    current: str | None = None
    if entity_id in pend:
        current = str((pend[entity_id].get("payload") or {}).get("physicalName") or "").strip() or None
    if current is None:
        pub = await repository.published(collection, {"_id": entity_id})
        if pub:
            current = str(pub[0].get("physicalName") or "").strip() or None
    if current == name:
        return None  # heredado sin cambios
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
    if collection == "relationships" and op == "upsert" and payload:
        # Se persiste el dump v2 NORMALIZADO (parent/child + pairs): el overlay
        # y el apply sirven/escriben el payload tal cual se grabó, así que un
        # shape legacy (source/target) no debe quedar grabado en el draft.
        payload = RelationshipDoc.model_validate({**payload, "id": entity_id}).model_dump()
        # N=N (doc 47): la relación migra la llave COMPLETA del padre.
        key_err = await _relationship_key_error(cs_id, payload)
        if key_err:
            raise RelationshipKeyMismatchError(key_err)
    # Unicidad de nombres (spec 10 §9): tablas por (schema, physicalName) y
    # columnas por physicalName dentro de su tableId — contra publicado activo
    # + pendientes de ESTE changeset. El router lo convierte en 409.
    dup = await _duplicate_error(cs_id, collection, entity_id, op, payload)
    if dup:
        raise DuplicateEntityError(dup)
    too_long = await _name_length_error(cs_id, collection, entity_id, op, payload)
    if too_long:
        raise NameTooLongError(too_long)
    updated = await repository.set_change(cs_id, collection, entity_id, op, payload)
    if updated is not None:
        return updated
    return "locked" if await repository.get(cs_id) else None


# Colecciones con chequeo de unicidad de nombres (spec 10 §9 + doc 18).
_UNIQUE_COLLS = ("canonical_tables", "canonical_columns", "schemas")


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
    # Dedupe por entidad (último gana): un cambio por (collection, entityId),
    # como quedaría tras el loop secuencial (el doc de cambio es único por key).
    deduped: dict[tuple[str, str], dict] = {}
    for it in items:
        deduped[(it["collection"], it["entityId"])] = dict(it)
    ordered = list(deduped.values())
    if not ordered:
        return cs

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
                    "canonical_tables", _table_dup_filter(p))
            elif it["collection"] == "canonical_columns" and p.get("tableId") and p["tableId"] not in pub_cols:
                pub_cols[p["tableId"]] = await repository.published(
                    "canonical_columns", {"tableId": p["tableId"]})
            elif it["collection"] == "schemas" and str(p.get("name") or "").strip():
                name = str(p["name"]).strip()
                if name.lower() not in pub_schemas:
                    pub_schemas[name.lower()] = await repository.published(
                        "schemas", {"name": {"$regex": f"^{re.escape(name)}$", "$options": "i"}})
        for coll, scope in (("canonical_tables", "table"), ("canonical_columns", "column")):
            if any(it["collection"] == coll for it in upserts):
                max_len[coll] = int((await settings_service.get_naming_for(scope)).get("maxLength") or 0)

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
                if current != name:
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
    nombre. Precede a `q`/`limit` (el Explorer pasa `schema`+`limit` juntos)."""
    cs = await repository.get(cs_id)
    if not cs:
        return None
    changes = (await repository.changes_map(cs_id, [collection])).get(collection, {})

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
        pub = await repository.published(collection, flt, limit=limit, sort_field=sort_field)

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
    pub = await repository.published(collection, flt)
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


async def _publish_duplicates(changes: dict) -> list[str]:
    """Re-chequeo COMPLETO de unicidad al publicar (spec 10 §9): cubre la
    carrera entre changesets concurrentes. Dos queries acotadas: tablas por
    $or de filtros indexados (schema+physicalName), columnas por $in de los
    tableIds tocados. Devuelve la lista de conflictos (vacía = ok)."""
    errors: list[str] = []

    tbl_changes = changes.get("canonical_tables") or {}
    tbl_upserts = {
        eid: ch for eid, ch in tbl_changes.items()
        if ch.get("op") != "delete"
        and str((ch.get("payload") or {}).get("physicalName") or "").strip()
    }
    if tbl_upserts:
        flt = {"$or": [_table_dup_filter(ch.get("payload") or {}) for ch in tbl_upserts.values()]}
        pub = await repository.published("canonical_tables", flt)
        for eid, ch in tbl_upserts.items():
            err = validation.duplicate_error("canonical_tables", eid, ch.get("payload") or {}, pub, tbl_changes)
            if err and err not in errors:
                errors.append(err)

    col_changes = changes.get("canonical_columns") or {}
    col_upserts = {
        eid: ch for eid, ch in col_changes.items()
        if ch.get("op") != "delete" and (ch.get("payload") or {}).get("tableId")
    }
    if col_upserts:
        tids = sorted({(ch.get("payload") or {})["tableId"] for ch in col_upserts.values()})
        pub = await repository.published("canonical_columns", {"tableId": {"$in": tids}})
        for eid, ch in col_upserts.items():
            err = validation.duplicate_error("canonical_columns", eid, ch.get("payload") or {}, pub, col_changes)
            if err and err not in errors:
                errors.append(err)

    sch_changes = changes.get("schemas") or {}
    sch_upserts = {
        eid: ch for eid, ch in sch_changes.items()
        if ch.get("op") != "delete"
        and str((ch.get("payload") or {}).get("name") or "").strip()
    }
    if sch_upserts:
        names = sorted({str((ch.get("payload") or {}).get("name") or "").strip()
                        for ch in sch_upserts.values()})
        flt = {"$or": [{"name": {"$regex": f"^{re.escape(n)}$", "$options": "i"}} for n in names]}
        pub = await repository.published("schemas", flt)
        for eid, ch in sch_upserts.items():
            err = validation.duplicate_error("schemas", eid, ch.get("payload") or {}, pub, sch_changes)
            if err and err not in errors:
                errors.append(err)
    return errors


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


async def _schema_usage(name: str, changes: dict) -> int:
    """Tablas + vistas EFECTIVAS (publicado + overlay del changeset) que usan
    el esquema `name`."""
    total = 0
    for coll in ("canonical_tables", "views"):
        pub = await repository.published(coll, {"schema": name})
        total += len(_overlay_by_schema(pub, changes.get(coll), name))
    return total


async def _publish_schema_deletes(changes: dict) -> list[str]:
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
        used = await _schema_usage(name, changes)
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
            await repository.published("canonical_tables", {"schema": old_name}),
            changes.get("canonical_tables"), old_name)
        views = _overlay_by_schema(
            await repository.published("views", {"schema": old_name}),
            changes.get("views"), old_name)
        # Pre-flight: si alguna tabla renombrada chocara con una homónima ya
        # existente en el esquema destino, se rechaza ENTERO con 409.
        for doc in tables:
            payload = {**{k: v for k, v in doc.items() if k != "id"}, "schema": new_name}
            dup = await _duplicate_error(cs_id, "canonical_tables", doc["id"], "upsert", payload)
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
        await repository.published("canonical_tables", {"schema": name}),
        changes.get("canonical_tables"), name)
    views = _overlay_by_schema(
        await repository.published("views", {"schema": name}),
        changes.get("views"), name)
    touched = {d["id"] for d in tables}
    for v in views:
        srcs = v.get("sourceTableIds") or ([v["tableId"]] if v.get("tableId") else [])
        touched.update(srcs)
    canvases = 0
    if touched:
        # subject_areas completa (28 canvases hoy): mismo tradeoff aceptado que
        # el effective de estructura (doc 16 §5 pendientes).
        sas = overlay(await repository.published("subject_areas"),
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
        changes = await repository.changes_map(cs_id, ["canonical_tables", "views"])
        used = await _schema_usage(name, changes)
        if used:
            return ("in-use", used)
    return await add_change(cs_id, actor, "schemas", schema_id, "delete", None)


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
    4. Si el apply/cascada FALLA (throttling o timeout), devuelve el
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
    # Re-chequeo de unicidad (spec 10 §9): otro changeset pudo publicar el
    # nombre entre el add_change y este apply. Mismo protocolo que el gate de
    # validación: revierte el claim y la producción queda intacta.
    dups = await _publish_duplicates(changes)
    if dups:
        await repository.transition(cs_id, fields.get("status", "approved"), _revert)
        raise DuplicateEntityError(
            "el request contiene nombres duplicados contra lo ya publicado — "
            + " | ".join(dups[:5])
        )
    # Guard de esquemas (doc 18): un delete de esquema con tablas/vistas
    # efectivas no publica — mismo protocolo (revierte el claim, 409).
    in_use = await _publish_schema_deletes(changes)
    if in_use:
        await repository.transition(cs_id, fields.get("status", "approved"), _revert)
        raise SchemaInUseError(
            "the request deletes schemas that are still in use — " + " | ".join(in_use[:5])
        )
    try:
        plan = apply_plan(changes)
        # Rollback (doc 16 §5d): capturar y estampar la imagen PREVIA de cada
        # entidad ANTES de aplicar — después del apply ya no existe el "antes".
        # `store_before_images` es no-op sobre cambios ya estampados (re-apply).
        await repository.store_before_images(
            cs_id, await repository.capture_before_images(plan))
        counts = await repository.apply_changes(plan)
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
    # Versiones publicadas DESPUÉS de la objetivo (latest→oldest). Ninguna ⇒ la
    # objetivo ES la producción actual: no hay rollback que hacer.
    after = await repository.applied_after(cs["appliedAt"])
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
    draft = await repository.create(
        f"Rollback a {label}", actor,
        extra={"description": f"Restaura el modelo al estado de {label}: deshace "
                              f"{n_ver} versión(es) posterior(es) sobre {len(inverse)} entidades."})
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


def build_diff_tree(collections: dict, changes: dict, published: dict,
                    sas: list[dict], projects: list[dict], folders: list[dict]) -> dict:
    """Jerarquía Proyecto→Folder→Canvas→Esquema→Tabla→Columnas (+Vistas) de los
    cambios de tablas/columnas/vistas, para el vistazo de la revisión (p5).

    Devuelve `{tree, orphans, structure, projectsAffected}`:
    - `tree`: nodos anidados por la jerarquía (una tabla en N canvases aparece
      en cada uno; las vistas cuelgan del/los canvas de sus fuentes).
    - `orphans`: tablas/vistas cambiadas sin canvas (no importadas a ningún ER).
    - `structure`: cambios de ESTRUCTURA (projects/folders/subject_areas/schemas
      como entidades) — su propio bloque, no encajan en el árbol de datos.
    - `projectsAffected`: nº de proyectos distintos tocados (canvas membership).
    Puro."""
    proj_name = {p["id"]: (p.get("name") or p["id"]) for p in projects}
    folder_by = {f["id"]: f for f in folders}
    sa_by_id = {sa["id"]: sa for sa in sas}

    def _kinds(coll: str) -> dict:
        b = collections.get(coll) or {}
        return {e["id"]: verb for verb in ("added", "edited", "deleted") for e in b.get(verb, [])}

    tbl_kind, col_kind, view_kind = _kinds("canonical_tables"), _kinds("canonical_columns"), _kinds("views")

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
        cols_by_table.setdefault(tid, []).append({"type": "column", "id": eid, "name": nm, "change": verb})

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
    projects_affected: set[str] = set()

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
            projects_affected.add(sa.get("projectId"))
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
                projects_affected.add(sa.get("projectId"))
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
                    s_children.append({"type": "schema", "id": schema, "name": schema, "children": kids})
                s_children.sort(key=lambda n: (n["name"] or "").lower())
                c_children.append({"type": "canvas", "id": sa_id,
                                   "name": (sa_by_id.get(sa_id) or {}).get("name") or sa_id, "children": s_children})
            c_children.sort(key=lambda n: (n["name"] or "").lower())
            fname = "— (project root)" if folder_id == "__root__" else ((folder_by.get(folder_id) or {}).get("name") or folder_id)
            f_children.append({"type": "folder", "id": folder_id, "name": fname, "children": c_children})
        f_children.sort(key=lambda n: (n["name"] or "").lower())
        tree.append({"type": "project", "id": proj_id or "—", "name": proj_name.get(proj_id) or proj_id or "—",
                     "children": f_children})
    tree.sort(key=lambda n: (n["name"] or "").lower())

    STRUCT = {"projects": "Project", "folders": "Folder", "subject_areas": "Canvas", "schemas": "Schema"}
    structure = []
    for coll, label in STRUCT.items():
        b = collections.get(coll) or {}
        for verb in ("added", "edited", "deleted"):
            for e in b.get(verb, []):
                structure.append({"type": coll, "id": e["id"], "name": e.get("name") or e["id"],
                                  "kind": label, "change": verb})

    return {"tree": tree, "orphans": orphan_tables + orphan_views, "structure": structure,
            "projectsAffected": len([p for p in projects_affected if p])}


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
            "relationships", {"$or": [{"parentTableId": {"$in": t}}, {"childTableId": {"$in": t}},
                                      {"sourceTableId": {"$in": t}}, {"targetTableId": {"$in": t}}]}
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
    # Jerarquía Proyecto→Folder→Canvas→Esquema→Tabla→Columnas (+Vistas) +
    # projectsAffected REAL (antes 0: leía el hint estático del changeset). La
    # estructura es chica (proyectos/folders/canvases) → overlay completo.
    sas = overlay(await repository.published("subject_areas"), changes.get("subject_areas") or {})
    projs = overlay(await repository.published("projects"), changes.get("projects") or {})
    flds = overlay(await repository.published("folders"), changes.get("folders") or {})
    tree = build_diff_tree(result["collections"], changes, published, sas, projs, flds)
    result["tree"] = tree["tree"]
    result["orphans"] = tree["orphans"]
    result["structure"] = tree["structure"]
    result["impact"]["projectsAffected"] = tree["projectsAffected"]
    return result


# ── Diff de campos por entidad (doc 31 — popup "Change details") ──────────


async def _detail_resolvers(wanted: list[tuple[str, str]], changes: dict,
                            before_by: dict[tuple[str, str], dict | None]) -> dict:
    """Mapas id→nombre para que el detalle sea legible: UDP, dominios, tablas/
    columnas referenciadas por relaciones y vistas, proyectos/folders. Slices
    puntuales — nunca colecciones completas de datos (solo estructura chica)."""
    from app.features.domains import repository as dom_repo
    from app.features.udp import repository as udp_repo

    cols = {c for c, _ in wanted}
    res: dict = {"udp": {}, "domains": {}, "tables": {}, "columns": {},
                 "projects": {}, "folders": {}}
    if cols & {"canonical_tables", "canonical_columns", "subject_areas"}:
        res["udp"] = {d["id"]: d["name"] for d in await udp_repo.list_udp()}
    if "canonical_columns" in cols:
        res["domains"] = {d["id"]: d["name"] for d in await dom_repo.list_domains()}

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
        res["projects"] = {d["id"]: d.get("name") or d["id"]
                           for d in await repository.published("projects")}
        _merge_change_names("projects", "projects", set(res["projects"]) | set(changes.get("projects") or {}))
    if "subject_areas" in cols:
        res["folders"] = {d["id"]: d.get("name") or d["id"]
                          for d in await repository.published("folders")}
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

    res = await _detail_resolvers(wanted, changes, before_by)
    return {"items": [diffdetail.entity_detail(c, e, changes[c][e], before_by[(c, e)], res)
                      for c, e in wanted]}


# Los compat M-series /approve y /reject ya NO tienen funciones propias: el
# router delega en `review()` (misma política de unanimidad + is_assigned).
# Antes `approve()` aplicaba con UNA sola aprobación, salteando la unanimidad.
