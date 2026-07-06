"""Negocio de `projects` / `subject_areas`. `merge_layout` y `tables_in_area` son puros."""
from __future__ import annotations

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

    sa = await repository.get_subject_area(sa_id)
    if not sa:
        return None
    ids: list[str] = sa.get("tableIds") or []
    id_set = set(ids)
    tables = await catalog_repo.list_tables_by_ids(ids)
    columns = await catalog_repo.list_columns_for_tables(ids)
    rels = await rel_repo.list_for_tables(ids)

    if changeset_id:
        ch = await cs_repo.changes_map(
            changeset_id, ["canonical_tables", "canonical_columns", "relationships"]
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
            rel_ch = {e: c for e, c in (ch.get("relationships") or {}).items()
                      if e in rel_ids
                      or (((c.get("payload") or {}).get("sourceTableId") in id_set)
                          and ((c.get("payload") or {}).get("targetTableId") in id_set))}
            tables = overlay(tables, tbl_ch)
            columns = overlay(columns, col_ch)
            rels = overlay(rels, rel_ch)
            # El overlay pierde el orden de lectura: restaurar el contrato
            # (tablas por nombre físico; columnas por tabla + ordinal).
            tables.sort(key=lambda t: (t.get("physicalName") or "").lower())
            columns.sort(key=lambda c: (c.get("tableId") or "", c.get("ordinal") or 0))

    visible = [r for r in rels if r.get("sourceTableId") in id_set and r.get("targetTableId") in id_set]
    return {"subjectArea": sa, "tables": tables, "columns": columns, "relationships": visible}
