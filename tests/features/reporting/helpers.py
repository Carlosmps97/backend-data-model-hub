"""Constantes y constructores compartidos por los tests del Reporting sobre una
versión propia (doc 102)."""
from __future__ import annotations

P = "p1"
AT = "2026-09-29T00:00:00+00:00"


def ch(coll: str, eid: str, op: str, payload: dict | None = None) -> dict:
    """Doc del ledger (`changeset_changes`) del draft `cs1`; el payload va con
    el `projectId` de `P`."""
    doc = {"_id": f"cs1::{coll}::{eid}", "csId": "cs1", "collection": coll, "entityId": eid, "op": op, "at": AT}
    if payload is not None:
        doc["payload"] = {"projectId": P, **payload}
    return doc
