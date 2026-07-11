"""Negocio de `relationships` (thin)."""
from __future__ import annotations

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
