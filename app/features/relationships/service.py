"""Negocio de `relationships` (thin) + impacto de eliminación de columna."""
from __future__ import annotations

from fastapi import HTTPException

from app.core.versioning import overlay
from app.features.catalog import repository as catalog_repo
from app.features.changesets import repository as cs_repo

from . import repository
from .models import RelationshipDoc
from .schemas import RelationshipBody


async def list_all() -> list[dict]:
    return await repository.list_all()


async def list_for_table(table_id: str) -> list[dict]:
    """Relaciones que tocan una tabla (cualquiera de los dos extremos)."""
    return await repository.list_for_tables([table_id])


async def create(body: RelationshipBody) -> dict:
    """Doc 75 I1/I2: el proyecto es el de la tabla padre; el hijo debe ser del
    mismo proyecto (una relación jamás cruza proyectos)."""
    data = body.model_dump()
    parent = data.get("parentTableId") or data.get("targetTableId")
    child = data.get("childTableId") or data.get("sourceTableId")
    pid = await catalog_repo.project_of_table(parent) if parent else None
    if not pid:
        raise HTTPException(status_code=409, detail="The parent table doesn't exist.")
    if child and await catalog_repo.project_of_table(child) != pid:
        raise HTTPException(status_code=409, detail="Tables belong to different projects.")
    return await repository.create({**data, "projectId": pid})


async def update(rid: str, body: RelationshipBody) -> dict | None:
    return await repository.update(rid, body.model_dump())


async def delete(rid: str) -> bool:
    return await repository.delete(rid)


# ── Impacto de eliminación de columna (spec 10 §8) ─────────────────────────


def _pairs_with(r: dict, column_id: str) -> list[dict]:
    """Pares de la relación donde la columna participa (padre o hijo). Puro."""
    return [p for p in (r.get("pairs") or [])
            if column_id in (p.get("parentColumnId"), p.get("childColumnId"))]


def impact_rows(column_id: str, rels: list[dict], table_names: dict[str, str],
                column_names: dict[str, str], canvases: list[dict]) -> list[dict]:
    """Filas del contrato de GET /api/relationships/impact. Cada relación se
    mira DESDE la columna consultada (`thisSide`: parent|child) y lista los
    canvases (subject areas que contienen la tabla propia) que ADEMÁS contienen
    la otra tabla — sólo ahí la relación es visible. Con pares múltiples, la
    fila sigue siendo POR RELACIÓN: `otherColumnName` junta las columnas del
    otro extremo de los pares tocados. Nombres desconocidos caen al id. Puro."""
    rows: list[dict] = []
    for r in rels:
        mine = _pairs_with(r, column_id)
        if not mine:
            continue
        this_parent = mine[0].get("parentColumnId") == column_id
        other_tid = r.get("childTableId") if this_parent else r.get("parentTableId")
        other_cids = [(p.get("childColumnId") if this_parent else p.get("parentColumnId"))
                      for p in mine]
        rows.append({
            "relId": r.get("id"),
            "otherTableId": other_tid,
            "otherTableName": table_names.get(other_tid, other_tid),
            "otherColumnId": other_cids[0],
            "otherColumnName": ", ".join(column_names.get(c, c) for c in other_cids),
            "thisSide": "parent" if this_parent else "child",
            "parentCardinality": r.get("parentCardinality"),
            "childCardinality": r.get("childCardinality"),
            "identifying": bool(r.get("identifying")),
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

    Contrato: {"relationships": [{relId, otherTableId, otherTableName,
    otherColumnId, otherColumnName, thisSide: "parent"|"child",
    parentCardinality, childCardinality, identifying, canvases: [{id, name}]}],
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
            if column_id in (p.get("sourceColumnId"), p.get("targetColumnId")):
                return True  # drafts legacy pre-v2
            return any(column_id in (pr.get("parentColumnId"), pr.get("childColumnId"))
                       for pr in (p.get("pairs") or []))

        rels = overlay(pub, {eid: ch for eid, ch in rel_changes.items()
                             if eid in in_slice or _touches(ch)})
    else:
        rels = pub
    # Normaliza (payloads legacy de drafts viejos) y re-filtra post-overlay:
    # un upsert pudo mover la relación a OTRA columna.
    rels = [RelationshipDoc.model_validate(r).model_dump() for r in rels]
    rels = [r for r in rels if _pairs_with(r, column_id)]
    if not rels:
        return {"relationships": [], "total": 0}

    other_tids: set = set()
    other_cids: set = set()
    for r in rels:
        mine = _pairs_with(r, column_id)
        this_parent = mine[0].get("parentColumnId") == column_id
        other_tids.add(r.get("childTableId") if this_parent else r.get("parentTableId"))
        other_cids.update((p.get("childColumnId") if this_parent else p.get("parentColumnId"))
                          for p in mine)
    table_names = await _names("canonical_tables", sorted(t for t in other_tids if t), changes)
    column_names = await _names("canonical_columns", sorted(c for c in other_cids if c), changes)

    # La tabla PROPIA de la columna: toda relación del slice la tiene de un lado.
    first = rels[0]
    first_pairs = _pairs_with(first, column_id)
    this_tid = (first["parentTableId"] if first_pairs[0].get("parentColumnId") == column_id
                else first["childTableId"])
    canvases = await repository.canvases_containing(this_tid)
    rows = impact_rows(column_id, rels, table_names, column_names, canvases)
    return {"relationships": rows, "total": len(rows)}


# ── Links de una tabla en TODOS los canvases (Properties · Links) ───────────


async def table_links(table_id: str, changeset_id: str | None = None) -> dict:
    """Relaciones ACTIVAS donde la tabla es padre o hijo — la tabla es
    CANÓNICA, así que incluye las visibles sólo en OTROS canvases (el panel
    del canvas actual sólo ve el slice del diagrama). Publicadas + overlay
    del changeset, con nombres resueltos (tablas y columnas de cada par) y
    los canvases donde la relación es visible (contienen AMBAS tablas).

    Contrato: {"links": [{id, parentTableId, parentTableName, childTableId,
    childTableName, parentCardinality, childCardinality, identifying,
    parentToChildPhrase, childToParentPhrase,
    pairs: [{parentColumnId, parentColumnName, childColumnId,
    childColumnName, roleName}], canvases: [{id, name}]}], "total": int}."""
    pub = await repository.list_for_tables([table_id])
    changes: dict = {}
    if changeset_id:
        changes = await cs_repo.changes_map(
            changeset_id,
            ["relationships", "canonical_tables", "canonical_columns", "subject_areas"])
        rel_changes = changes.get("relationships", {})
        in_slice = {r["id"] for r in pub}

        def _touches(ch: dict) -> bool:
            p = ch.get("payload") or {}
            return table_id in (p.get("parentTableId"), p.get("childTableId"),
                                p.get("sourceTableId"), p.get("targetTableId"))

        rels = overlay(pub, {eid: ch for eid, ch in rel_changes.items()
                             if eid in in_slice or _touches(ch)})
    else:
        rels = pub
    # Normaliza (payloads legacy) y re-filtra post-overlay: un upsert pudo
    # mover la relación a OTRA tabla.
    rels = [RelationshipDoc.model_validate(r).model_dump() for r in rels]
    rels = [r for r in rels
            if table_id in (r.get("parentTableId"), r.get("childTableId"))]
    if not rels:
        return {"links": [], "total": 0}

    tids = sorted({t for r in rels
                   for t in (r.get("parentTableId"), r.get("childTableId")) if t})
    cids = sorted({c for r in rels for p in (r.get("pairs") or [])
                   for c in (p.get("parentColumnId"), p.get("childColumnId")) if c})
    table_names = await _names("canonical_tables", tids, changes)
    column_names = await _names("canonical_columns", cids, changes)
    canvases = await repository.canvases_containing(table_id)
    # Overlay de canvases (doc 21b): un canvas del DRAFT que contiene la tabla
    # aún no está publicado — sin esto la relación no listaba ese canvas.
    sa_changes = changes.get("subject_areas") or {}
    if sa_changes:
        in_canvas = {c["id"] for c in canvases}

        def _sa_touches(ch: dict) -> bool:
            return table_id in ((ch.get("payload") or {}).get("tableIds") or [])

        canvases = overlay(canvases, {eid: ch for eid, ch in sa_changes.items()
                                      if eid in in_canvas or _sa_touches(ch)})
        canvases = [c for c in canvases if table_id in (c.get("tableIds") or [])]

    links = []
    for r in rels:
        other_tid = (r.get("childTableId") if r.get("parentTableId") == table_id
                     else r.get("parentTableId"))
        links.append({
            "id": r.get("id"),
            "parentTableId": r.get("parentTableId"),
            "parentTableName": table_names.get(r.get("parentTableId"), r.get("parentTableId")),
            "childTableId": r.get("childTableId"),
            "childTableName": table_names.get(r.get("childTableId"), r.get("childTableId")),
            "parentCardinality": r.get("parentCardinality"),
            "childCardinality": r.get("childCardinality"),
            "identifying": bool(r.get("identifying")),
            # Doc 98: frases de relación (la etiqueta del wire).
            "parentToChildPhrase": r.get("parentToChildPhrase"),
            "childToParentPhrase": r.get("childToParentPhrase"),
            "pairs": [{
                "parentColumnId": p.get("parentColumnId"),
                "parentColumnName": column_names.get(p.get("parentColumnId"), p.get("parentColumnId")),
                "childColumnId": p.get("childColumnId"),
                "childColumnName": column_names.get(p.get("childColumnId"), p.get("childColumnId")),
                "roleName": p.get("roleName"),
            } for p in (r.get("pairs") or [])],
            "canvases": [{"id": c["id"], "name": c["name"]} for c in canvases
                         if other_tid in (c.get("tableIds") or [])],
        })
    return {"links": links, "total": len(links)}
