"""Negocio de `projects` / `subject_areas`. `merge_layout` y `tables_in_area` son puros."""
from __future__ import annotations

from fastapi import HTTPException

from app.core.audit import audit
from app.features.changesets import service as cs_service
from app.features.data_standards import service as std_service

from . import repository
from .schemas import SubjectAreaBody


class DuplicateProjectError(ValueError):
    """Ya existe un proyecto con ese nombre (case-insensitive). Router → 409."""


def merge_layout(layout: dict, updates: dict) -> dict:
    """Actualiza posiciones por tabla conservando las demás. Puro."""
    return {**layout, **updates}


def tables_in_area(all_tables: list[dict], table_ids: list[str]) -> list[dict]:
    """Filtra el pool canónico a las tablas referenciadas por la Subject Area. Puro."""
    wanted = set(table_ids)
    return [t for t in all_tables if t["id"] in wanted]


async def list_projects() -> list[dict]:
    return await repository.list_projects()


async def create_project(actor: str, body) -> dict:
    """Doc 75 D5/D15: proyecto nuevo = doc + estándares (vacíos o copiados de
    otro proyecto) + marcador `v1`, en UNA request. Nombre único (409)."""
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="The project name is required.")
    if await repository.find_by_name(name):
        raise DuplicateProjectError(f"A project named '{name}' already exists.")
    copy_from = None
    if body.copyFrom:
        src = await repository.get_project(body.copyFrom.projectId)
        if not src:
            raise HTTPException(status_code=404, detail="Source project not found.")
        copy_from = {"projectId": src["id"], "blocks": list(body.copyFrom.blocks), "projectName": src["name"]}
    project = await repository.create_project({"name": name, "description": body.description})
    try:
        await std_service.bootstrap_project(actor, project["id"], copy_from)
        await cs_service.create_base_marker(project["id"])
    except Exception:
        # Nunca dejar un proyecto a medias visible: se retira y se re-lanza.
        await repository.discard_project(project["id"])
        raise
    await audit(actor, "project.create", target=project["id"], target_type="project",
                meta={"name": name, "copiedFrom": copy_from["projectId"] if copy_from else None,
                      "blocks": copy_from["blocks"] if copy_from else []})
    return project


async def scope_counts(pid: str) -> dict[str, int]:
    return await repository.count_scope(pid)


async def list_subject_areas(project_id: str, folder_id: str | None = None) -> list[dict]:
    return await repository.list_subject_areas(project_id, folder_id)


async def get_subject_area(sa_id: str) -> dict | None:
    return await repository.get_subject_area(sa_id)


async def create_subject_area(body: SubjectAreaBody) -> dict:
    return await repository.create_subject_area(body.model_dump(exclude_none=True))


async def update_subject_area(sa_id: str, body: SubjectAreaBody) -> dict | None:
    return await repository.update_subject_area(sa_id, body.model_dump(exclude_none=True))


async def set_tables(sa_id: str, table_ids: list[str]) -> dict | None:
    return await repository.update_subject_area(sa_id, {"tableIds": table_ids})


async def set_views(sa_id: str, view_ids: list[str]) -> dict | None:
    """Doc 70: reemplaza la membresía de vistas del canvas (lista explícita)."""
    return await repository.update_subject_area(sa_id, {"viewIds": list(dict.fromkeys(view_ids))})


async def set_layout(sa_id: str, updates: dict) -> dict | None:
    sa = await repository.get_subject_area(sa_id)
    if not sa:
        return None
    return await repository.update_subject_area(sa_id, {"layout": merge_layout(sa.get("layout") or {}, updates)})


async def set_drawings(sa_id: str, drawings: list[dict]) -> dict | None:
    """Persiste la capa DRAWING del canvas (formas/texto). Reemplaza el array completo."""
    return await repository.update_subject_area(sa_id, {"drawings": drawings})


async def delete_subject_area(sa_id: str) -> bool:
    return await repository.delete_subject_area(sa_id)


async def diagram(sa_id: str, changeset_id: str | None = None) -> dict | None:
    """Diagrama completo del canvas en 4 queries batched (`$in`) — reemplaza el
    camino viejo (pool COMPLETO de tablas + una query POR tabla + todas las
    relaciones del sistema), inviable a 15k tablas.

    Con `changeset_id` aplica el overlay del working copy SERVER-SIDE (acotado
    al slice del canvas), de modo que el frontend arma el canvas con UNA sola
    request tanto en modo publicado como en modo edición/visor."""
    from app.core.versioning import overlay
    from app.features.catalog import repository as catalog_repo
    from app.features.changesets import repository as cs_repo
    from app.features.relationships import repository as rel_repo
    from app.features.relationships.models import RelationshipDoc
    from app.features.views import repository as views_repo
    from app.features.views.membership import filter_canvas_views, view_sources

    sa = await repository.get_subject_area(sa_id)
    if changeset_id:
        # La ESTRUCTURA también es versionada (doc 16): el canvas puede ser un
        # draft del changeset (no existe publicado) o tener membresía/layout
        # pendientes — el doc efectivo del canvas sale del overlay.
        sa_ch = (await cs_repo.changes_map(changeset_id, ["subject_areas"])
                 ).get("subject_areas", {}).get(sa_id)
        if sa_ch:
            if sa_ch.get("op") == "delete":
                return None
            sa = {**(sa_ch.get("payload") or {}), "id": sa_id}
    if not sa:
        return None
    ids: list[str] = sa.get("tableIds") or []
    id_set = set(ids)
    tables = await catalog_repo.list_tables_by_ids(ids)
    columns = await catalog_repo.list_columns_for_tables(ids)
    rels = await rel_repo.list_for_tables(ids)
    # Doc 70: vistas MIEMBRO del canvas (`viewIds`), con fallback legacy a la
    # regla F3/D3 (showOnCanvas + ≥1 fuente presente) cuando el canvas nunca
    # materializó su lista. Vistas VERSIONADAS (doc 20): overlay abajo.
    explicit = sa.get("viewIds")
    views = await views_repo.resolve_canvas_views(sa)

    if changeset_id:
        ch = await cs_repo.changes_map(
            changeset_id, ["canonical_tables", "canonical_columns", "relationships", "views"]
        )
        if ch:
            # Cambios acotados al slice del canvas: entidades ya presentes, o
            # nuevas cuyo payload apunta a tablas del canvas (una columna nueva
            # de OTRA tabla no debe colarse en este diagrama).
            tbl_ch = {e: c for e, c in (ch.get("canonical_tables") or {}).items() if e in id_set}
            col_ids = {c["id"] for c in columns}
            col_ch = {e: c for e, c in (ch.get("canonical_columns") or {}).items()
                      if e in col_ids or ((c.get("payload") or {}).get("tableId") in id_set)}
            rel_ids = {r["id"] for r in rels}

            def _rel_in_canvas(c: dict) -> bool:
                p = c.get("payload") or {}
                parent = p.get("parentTableId") or p.get("sourceTableId")
                child = p.get("childTableId") or p.get("targetTableId")
                return parent in id_set and child in id_set

            rel_ch = {e: c for e, c in (ch.get("relationships") or {}).items()
                      if e in rel_ids or _rel_in_canvas(c)}
            tables = overlay(tables, tbl_ch)
            columns = overlay(columns, col_ch)
            # Normaliza post-overlay: un payload legacy de un draft viejo debe
            # salir al canvas ya en shape v2 (parent/child + pairs).
            rels = [RelationshipDoc.model_validate(r).model_dump()
                    for r in overlay(rels, rel_ch)]
            # Vistas del changeset (doc 20). Canvas con membresía EXPLÍCITA
            # (doc 70): sólo entran los cambios de sus miembros. Canvas legacy:
            # entran las pendientes cuyo payload tiene fuentes en el canvas.
            # El re-filtro (`filter_canvas_views`) saca borradas / re-apuntadas
            # fuera del canvas / no miembros.
            view_ids = {v["id"] for v in views}
            if explicit is not None:
                wanted = set(explicit)
                view_ch = {e: c for e, c in (ch.get("views") or {}).items() if e in wanted}
            else:
                view_ch = {e: c for e, c in (ch.get("views") or {}).items()
                           if e in view_ids
                           or (set(view_sources(c.get("payload") or {})) & id_set)}
            if view_ch:
                views = filter_canvas_views(overlay(views, view_ch), explicit, ids)
            # El overlay pierde el orden de lectura: restaurar el contrato
            # (tablas por nombre físico; columnas por tabla + ordinal).
            tables.sort(key=lambda t: (t.get("physicalName") or "").lower())
            columns.sort(key=lambda c: (c.get("tableId") or "", c.get("ordinal") or 0))

    visible = [r for r in rels if r.get("parentTableId") in id_set and r.get("childTableId") in id_set]
    # Doc 70: el canvas sale con `viewIds` MATERIALIZADO — un canvas legacy
    # (None) devuelve la lista que hoy se ve, así el front siempre parte de una
    # lista explícita para sus mutaciones de membresía (import, add, remove).
    sa_out = {**sa, "viewIds": list(explicit) if explicit is not None else [v["id"] for v in views]}
    return {"subjectArea": sa_out, "tables": tables, "columns": columns,
            "relationships": visible, "views": views}
