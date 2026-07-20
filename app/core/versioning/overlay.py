"""Overlay de un changeset sobre el estado publicado, y resumen de diff. Puro.

`changes` (de UNA colección): { entityId: { "op": "upsert"|"delete", "payload"?: {...} } }.
"""
from __future__ import annotations


def overlay(published: list[dict], changes: dict) -> list[dict]:
    """Estado efectivo = publicado con upserts aplicados, deletes quitados,
    nuevos agregados. Cada entidad lleva su `id`."""
    by_id: dict = {e["id"]: e for e in published}
    for eid, ch in changes.items():
        if ch.get("op") == "delete":
            by_id.pop(eid, None)
        else:  # upsert
            by_id[eid] = {**(ch.get("payload") or {}), "id": eid}
    return list(by_id.values())


def summarize_diff(published: list[dict], changes: dict) -> dict:
    """Clasifica cada cambio vs lo publicado: added / modified / removed (ids)."""
    pub_ids = {e["id"] for e in published}
    added, modified, removed = [], [], []
    for eid, ch in changes.items():
        if ch.get("op") == "delete":
            if eid in pub_ids:
                removed.append(eid)
        elif eid in pub_ids:
            modified.append(eid)
        else:
            added.append(eid)
    return {"added": added, "modified": modified, "removed": removed}
