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


async def diagram(sa_id: str) -> dict | None:
    from app.features.catalog import repository as catalog_repo
    from app.features.relationships import repository as rel_repo

    sa = await repository.get_subject_area(sa_id)
    if not sa:
        return None
    tables = tables_in_area(await catalog_repo.list_tables(), sa["tableIds"])
    columns: list[dict] = []
    for t in tables:
        columns.extend(await catalog_repo.list_columns(t["id"]))
    rels = await rel_repo.list_all()
    ids = set(sa["tableIds"])
    visible = [r for r in rels if r["sourceTableId"] in ids and r["targetTableId"] in ids]
    return {"subjectArea": sa, "tables": tables, "columns": columns, "relationships": visible}
