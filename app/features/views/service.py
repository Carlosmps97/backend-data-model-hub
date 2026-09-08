"""Negocio de `views` (thin)."""
from __future__ import annotations

from fastapi import HTTPException

from app.core.versioning.overlay import overlay
from app.features.catalog import repository as catalog_repo
from app.features.changesets import repository as cs_repo

from . import repository
from .custom_sql import parse_custom_sql
from .membership import view_on_canvas, view_sources
from .models import normalize_source_tables
from .schemas import ViewBody


def _with_custom_sql(doc: dict) -> dict:
    """Doc 61: normaliza el modo Personalizada. customSql vacío → None +
    customColumns limpias (modo Regular); con script → RE-deriva customColumns
    del parse (no se confía en el cliente). Levanta CustomSqlError si el
    script no parsea (el router lo convierte en 422). Pura."""
    sql = (doc.get("customSql") or "").strip()
    if not sql:
        return {**doc, "customSql": None, "customColumns": []}
    return {**doc, "customSql": sql,
            "customColumns": parse_custom_sql(sql)["columns"]}


async def list_all(table_id: str | None = None, table_ids: list[str] | None = None,
                   project_id: str | None = None) -> list[dict]:
    return await repository.list_all(table_id, table_ids, project_id)


async def create(body: ViewBody) -> dict:
    # by_alias: `schema` llega como `schema` al repo (no `sql_schema`). El SQL
    # custom se valida ANTES de tocar la BD (422 fail-fast).
    data = _with_custom_sql(body.model_dump(by_alias=True))
    # Doc 75 I1: el proyecto de la vista es el de su PRIMERA tabla fuente (lo
    # estampa el servidor; nunca viene del cliente).
    sources = body.sourceTableIds or ([body.tableId] if body.tableId else [])
    project_id = await catalog_repo.project_of_table(sources[0]) if sources else None
    if not project_id:
        raise HTTPException(status_code=409, detail="The view's source table doesn't exist.")
    return await repository.create({**data, "projectId": project_id})


async def update(vid: str, body: ViewBody) -> dict | None:
    # Normaliza ANTES del $set: repository.update escribe el dict tal cual a
    # Mongo (solo la RESPUESTA se re-valida vía ViewDoc); sin esto el doc
    # persistido quedaría con tableId/sourceTableIds inconsistentes.
    data = _with_custom_sql(normalize_source_tables(body.model_dump(by_alias=True)))
    cur = await repository.get(vid)
    if cur is None:
        return None
    # El proyecto se conserva del doc vivo (doc 75 I1).
    return await repository.update(vid, {**data, "projectId": cur["projectId"]})


async def delete(vid: str) -> bool:
    return await repository.delete(vid)


async def table_views(table_id: str, changeset_id: str | None = None) -> dict:
    """Vistas GLOBALES de una tabla (doc 70 §2.4, espejo de `relationships/links`):
    la tabla es canónica, así que incluye las vistas que viven sólo en OTROS
    canvases (el panel del canvas actual sólo ve `diagram.views`). Publicadas +
    overlay del changeset (vistas nuevas/renombradas del draft), cada una con
    los canvases donde es MIEMBRO visible (`membership.view_on_canvas`:
    explícito por `viewIds`, o regla legacy en canvases sin lista).

    Contrato: {"views": [{…view, "canvases": [{id, name}]}], "total": int}."""
    pub = await repository.list_all(table_id)
    changes: dict = {}
    views = pub
    if changeset_id:
        changes = await cs_repo.changes_map(changeset_id, ["views", "subject_areas"])
        view_ch = changes.get("views") or {}
        in_slice = {v["id"] for v in pub}

        def _touches(ch: dict) -> bool:
            return table_id in view_sources(ch.get("payload") or {})

        merged = overlay(pub, {e: c for e, c in view_ch.items() if e in in_slice or _touches(c)})
        views = [repository.normalize(v) for v in merged]
    # Re-filtro post-overlay: un upsert pudo re-apuntar la vista a otra tabla.
    views = [v for v in views if table_id in view_sources(v)]
    canvases = await repository.canvases_for_views([v["id"] for v in views], table_id)
    sa_ch = changes.get("subject_areas") or {}
    if sa_ch:
        # Canvases del DRAFT (aún no publicados) que contienen la tabla o listan
        # alguna de estas vistas — sin esto la vista no mostraría ese canvas.
        known = {c["id"] for c in canvases}
        vids = {v["id"] for v in views}

        def _sa_touches(ch: dict) -> bool:
            p = ch.get("payload") or {}
            return table_id in (p.get("tableIds") or []) or bool(vids & set(p.get("viewIds") or []))

        canvases = overlay(canvases, {e: c for e, c in sa_ch.items() if e in known or _sa_touches(c)})
    rows = []
    for v in views:
        on = [{"id": c["id"], "name": c.get("name") or ""} for c in canvases if view_on_canvas(v, c)]
        on.sort(key=lambda c: c["name"].lower())
        rows.append({**v, "canvases": on})
    rows.sort(key=lambda v: ((v.get("schema") or ""), (v.get("name") or "").lower()))
    return {"views": rows, "total": len(rows)}
