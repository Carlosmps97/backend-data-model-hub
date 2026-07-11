"""Negocio de `relationships` (thin) + impacto de eliminación de columna."""
from __future__ import annotations

from app.core.versioning import overlay
from app.features.changesets import repository as cs_repo

from . import repository
from .schemas import RelationshipBody


async def list_all() -> list[dict]:
    return await repository.list_all()


async def list_for_table(table_id: str) -> list[dict]:
    """Relaciones que tocan una tabla (cualquiera de los dos extremos)."""
    return await repository.list_for_tables([table_id])


async def create(body: RelationshipBody) -> dict:
    return await repository.create(body.model_dump())


async def update(rid: str, body: RelationshipBody) -> dict | None:
    return await repository.update(rid, body.model_dump())


async def delete(rid: str) -> bool:
    return await repository.delete(rid)


# ── Impacto de eliminación de columna (spec 10 §8) ─────────────────────────


def impact_rows(column_id: str, rels: list[dict], table_names: dict[str, str],
                column_names: dict[str, str], canvases: list[dict]) -> list[dict]:
    """Filas del contrato de GET /api/relationships/impact. Cada relación se
    mira DESDE la columna consultada (`thisSide`) y lista los canvases
    (subject areas que contienen la tabla propia) que ADEMÁS contienen la otra
    tabla — sólo ahí la relación es visible. Nombres desconocidos caen al id.
    Puro."""
    rows: list[dict] = []
    for r in rels:
        this_src = r.get("sourceColumnId") == column_id
        other_tid = r.get("targetTableId") if this_src else r.get("sourceTableId")
        other_cid = r.get("targetColumnId") if this_src else r.get("sourceColumnId")
        rows.append({
            "relId": r.get("id"),
            "otherTableId": other_tid,
            "otherTableName": table_names.get(other_tid, other_tid),
            "otherColumnId": other_cid,
            "otherColumnName": column_names.get(other_cid, other_cid),
            "thisSide": "source" if this_src else "target",
            "sourceCardinality": r.get("sourceCardinality"),
            "targetCardinality": r.get("targetCardinality"),
            "canvases": [{"id": c["id"], "name": c["name"]}
                         for c in canvases if other_tid in (c.get("tableIds") or [])],
        })
    return rows


async def _names(collection: str, ids: list[str], changes: dict) -> dict[str, str]:
    """Nombre físico|lógico por id: publicado + payloads del changeset (cubre
    tablas/columnas NUEVAS aún no publicadas)."""
    wanted = [i for i in ids if i]
    if not wanted:
        return {}
    docs = await cs_repo.published(collection, {"_id": {"$in": wanted}})
    names = {d["id"]: str(d.get("physicalName") or d.get("logicalName") or d["id"]) for d in docs}
    for eid, ch in (changes.get(collection) or {}).items():
        if eid in wanted and ch.get("op") != "delete":
            p = ch.get("payload") or {}
            names[eid] = str(p.get("physicalName") or p.get("logicalName") or eid)
    return names


async def column_impact(column_id: str, changeset_id: str | None = None) -> dict:
    """Impacto GLOBAL de eliminar una columna (spec 10 §8): relaciones activas
    donde es extremo — publicadas + overlay del changeset —, con el otro
    extremo enriquecido (tabla.columna) y los canvases (`subject_areas`) que
    contienen AMBAS tablas de la relación.

    Contrato (fijo): {"relationships": [{relId, otherTableId, otherTableName,
    otherColumnId, otherColumnName, thisSide: "source"|"target",
    sourceCardinality, targetCardinality, canvases: [{id, name}]}],
    "total": int}."""
    pub = await repository.list_for_column(column_id)
    changes: dict = {}
    if changeset_id:
        changes = await cs_repo.changes_map(
            changeset_id, ["relationships", "canonical_tables", "canonical_columns"])
        rel_changes = changes.get("relationships", {})
        in_slice = {r["id"] for r in pub}

        def _touches(ch: dict) -> bool:
            p = ch.get("payload") or {}
            return column_id in (p.get("sourceColumnId"), p.get("targetColumnId"))

        rels = overlay(pub, {eid: ch for eid, ch in rel_changes.items()
                             if eid in in_slice or _touches(ch)})
    else:
        rels = pub
    # Re-filtro post-overlay: un upsert pudo mover la relación a OTRA columna.
    rels = [r for r in rels if column_id in (r.get("sourceColumnId"), r.get("targetColumnId"))]
    if not rels:
        return {"relationships": [], "total": 0}

    other_tids: set = set()
    other_cids: set = set()
    for r in rels:
        this_src = r.get("sourceColumnId") == column_id
        other_tids.add(r.get("targetTableId") if this_src else r.get("sourceTableId"))
        other_cids.add(r.get("targetColumnId") if this_src else r.get("sourceColumnId"))
    table_names = await _names("canonical_tables", sorted(t for t in other_tids if t), changes)
    column_names = await _names("canonical_columns", sorted(c for c in other_cids if c), changes)

    # La tabla PROPIA de la columna: toda relación del slice la tiene de un lado.
    first = rels[0]
    this_tid = first["sourceTableId"] if first.get("sourceColumnId") == column_id else first["targetTableId"]
    canvases = await repository.canvases_containing(this_tid)
    rows = impact_rows(column_id, rels, table_names, column_names, canvases)
    return {"relationships": rows, "total": len(rows)}
