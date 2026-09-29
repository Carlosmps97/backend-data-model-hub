"""Overlay de un changeset sobre el estado publicado, y resumen de diff. Puro.

`changes` (de UNA colección): { entityId: { "op": "upsert"|"delete", "payload"?: {...} } }.
"""
from __future__ import annotations


def reserved_key(key: str) -> bool:
    """¿La llave de PRIMER nivel significa otra cosa para el `$set` del publish?
    `a.b` es un camino (escribe DENTRO de `a`), `$x` un operador de la base y
    `_id` la identidad del registro. Ningún campo de los modelos se llama así:
    esas llaves no se aceptan, no se devuelven ni se publican (doc 100, P1/P2).
    Puro."""
    return "." in key or key.startswith("$") or key == "_id"


def plain(doc: dict) -> dict:
    """El documento sin llaves reservadas (el MISMO objeto si no trae
    ninguna: el camino común no copia). Puro."""
    if not any(reserved_key(k) for k in doc):
        return doc
    return {k: v for k, v in doc.items() if not reserved_key(k)}


def overlay(published: list[dict], changes: dict) -> list[dict]:
    """Estado efectivo = publicado con upserts aplicados, deletes quitados,
    nuevos agregados. Cada entidad lleva su `id`.

    Doc 100: sin llaves reservadas — el cliente arma cada guardado sobre este
    estado y mandaría de vuelta una llave que la entrada rechaza (un cambio o
    un documento grabados antes del arreglo dejarían la entidad sin poder
    editarse)."""
    by_id: dict = {e["id"]: plain(e) for e in published}
    for eid, ch in changes.items():
        if ch.get("op") == "delete":
            by_id.pop(eid, None)
        else:  # upsert
            by_id[eid] = {**plain(ch.get("payload") or {}), "id": eid}
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
