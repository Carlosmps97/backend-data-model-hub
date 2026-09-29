"""Doc 102 — la versión PROPIA (draft / en revisión) superpuesta a lo publicado
para el Reporting. Funciones PURAS con la MISMA semántica que el canvas y el
Explorer (docs 70/72, `app.core.versioning.overlay`): un upsert reemplaza el
documento, un delete lo quita y las altas del proyecto entran. Solo entran los
cambios que tocan el alcance pedido (entidades ya leídas o upserts del
proyecto que caen en él); un re-filtro final saca lo que la versión movió
fuera del alcance."""
from __future__ import annotations

from app.core.versioning import overlay
from app.features.views.membership import view_sources


def changes_of(changes: dict | None, collection: str) -> dict:
    """Cambios de UNA colección ({entityId: cambio}); {} sin versión. Puro."""
    return (changes or {}).get(collection) or {}


def _upsert_of(ch: dict, project_id: str) -> bool:
    """¿Upsert cuyo documento es de este proyecto? (alta, revive o edición de
    algo que no estaba en la lectura). Puro."""
    return ch.get("op") != "delete" and (ch.get("payload") or {}).get("projectId") == project_id


def overlay_project(published: list[dict], changes: dict, project_id: str) -> list[dict]:
    """Colección del proyecto con la versión aplicada. Puro."""
    if not changes:
        return published
    known = {d["id"] for d in published}
    return overlay(published, {eid: ch for eid, ch in changes.items()
                               if eid in known or _upsert_of(ch, project_id)})


def overlay_columns(published: list[dict], changes: dict, project_id: str,
                    table_ids: set[str]) -> list[dict]:
    """Columnas del LOTE `table_ids` (vacío = sin lote, todo el proyecto) con
    la versión aplicada. Puro."""
    if not changes:
        return published
    known = {c["id"] for c in published}

    def _in_lot(doc: dict) -> bool:
        return not table_ids or doc.get("tableId") in table_ids

    touching = {eid: ch for eid, ch in changes.items()
                if eid in known or (_upsert_of(ch, project_id) and _in_lot(ch.get("payload") or {}))}
    return [c for c in overlay(published, touching) if _in_lot(c)]


def overlay_views(published: list[dict], changes: dict, project_id: str,
                  table_ids: set[str], schema: str | None = None) -> list[dict]:
    """Vistas derivadas de `table_ids` (o de `schema`; sin ninguno, todas) con
    la versión aplicada. Puro."""
    if not changes:
        return published
    known = {v["id"] for v in published}

    def _in_scope(doc: dict) -> bool:
        if table_ids:
            return bool(set(view_sources(doc)) & table_ids)
        if schema:
            return doc.get("schema") == schema
        return True

    touching = {eid: ch for eid, ch in changes.items()
                if eid in known or (_upsert_of(ch, project_id) and _in_scope(ch.get("payload") or {}))}
    return [v for v in overlay(published, touching) if _in_scope(v)]


def overlay_named(docs: dict[str, dict], changes: dict, ids) -> dict[str, dict]:
    """{id: doc} (nombres de tablas o columnas) con los renombres, altas y
    bajas de la versión para esos `ids`. No muta la entrada. Puro."""
    out = dict(docs)
    for eid in ids:
        ch = changes.get(eid)
        if ch is None:
            continue
        if ch.get("op") == "delete":
            out.pop(eid, None)
        else:
            out[eid] = ch.get("payload") or {}
    return out
