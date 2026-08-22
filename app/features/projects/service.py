"""Negocio de `projects` / `subject_areas`. `merge_layout` y `tables_in_area` son puros."""
from __future__ import annotations

from app.core.audit import audit

from . import repository
from .schemas import ProjectBody, SubjectAreaBody


def merge_layout(layout: dict, updates: dict) -> dict:
    """Actualiza posiciones por tabla conservando las demás. Puro."""
    return {**layout, **updates}


def tables_in_area(all_tables: list[dict], table_ids: list[str]) -> list[dict]:
    """Filtra el pool canónico a las tablas referenciadas por la Subject Area. Puro."""
    wanted = set(table_ids)
    return [t for t in all_tables if t["id"] in wanted]


async def list_projects() -> list[dict]:
    return await repository.list_projects()


async def create_project(body: ProjectBody) -> dict:
    return await repository.create_project(body.model_dump(exclude_none=True))


async def update_project(pid: str, body: ProjectBody) -> dict | None:
    return await repository.update_project(pid, body.model_dump(exclude_none=True))


async def delete_project(pid: str) -> bool:
    return await repository.delete_project(pid)


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


async def set_layout(sa_id: str, updates: dict) -> dict | None:
    sa = await repository.get_subject_area(sa_id)
    if not sa:
        return None
    return await repository.update_subject_area(sa_id, {"layout": merge_layout(sa.get("layout") or {}, updates)})


async def set_drawings(sa_id: str, drawings: list[dict]) -> dict | None:
    """Persiste la capa DRAWING del canvas (formas/texto). Reemplaza el array completo."""
    return await repository.update_subject_area(sa_id, {"drawings": drawings})


async def set_udp_values(sa_id: str, values: dict[str, str], actor: str) -> dict | None:
    """F5 — persiste el mapa completo de UDP del canvas (mutación DIRECTA, mismo
    régimen que layout/drawings: sin changeset) y audita con verbo específico."""
    sa = await repository.update_subject_area(sa_id, {"udpValues": values})
    if sa is not None:
        await audit(actor, "canvas.udp.update", target=sa_id, target_type="subject_area")
    return sa


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
    # F3: vistas visibles en este canvas (showOnCanvas + ≥1 fuente presente).
    # Vistas VERSIONADAS (doc 20): con changeset se aplica su overlay abajo.
    views = await views_repo.list_for_canvas(ids)

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
            # Vistas del changeset (doc 20): entran las pendientes cuyo payload
            # tiene fuentes en el canvas; el re-filtro de abajo saca borradas /
            # ocultadas / re-apuntadas fuera del canvas.
            view_ids = {v["id"] for v in views}

            def _view_srcs(p: dict) -> list:
                return p.get("sourceTableIds") or ([p.get("tableId")] if p.get("tableId") else [])

            view_ch = {e: c for e, c in (ch.get("views") or {}).items()
                       if e in view_ids
                       or (set(_view_srcs(c.get("payload") or {})) & id_set)}
            if view_ch:
                views = [v for v in overlay(views, view_ch)
                         if v.get("showOnCanvas")
                         and set(_view_srcs(v)) & id_set]
            # El overlay pierde el orden de lectura: restaurar el contrato
            # (tablas por nombre físico; columnas por tabla + ordinal).
            tables.sort(key=lambda t: (t.get("physicalName") or "").lower())
            columns.sort(key=lambda c: (c.get("tableId") or "", c.get("ordinal") or 0))

    visible = [r for r in rels if r.get("parentTableId") in id_set and r.get("childTableId") in id_set]
    return {"subjectArea": sa, "tables": tables, "columns": columns,
            "relationships": visible, "views": views}
