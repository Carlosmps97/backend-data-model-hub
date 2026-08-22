"""Negocio de `folders`. `descendant_ids` es puro (cascada testeable sin store)."""
from __future__ import annotations

from . import repository
from .schemas import FolderCreateBody, FolderRenameBody


def descendant_ids(folders: list[dict], root_id: str) -> list[str]:
    """IDs de `root_id` + todas sus subcarpetas (transitivo). Puro.

    `folders` es la lista de carpetas del proyecto (`{id, parentFolderId, ...}`).
    Se incluye `root_id` aunque no esté en la lista. Tolera ciclos (visita cada
    id una sola vez)."""
    children: dict[str | None, list[str]] = {}
    for f in folders:
        children.setdefault(f.get("parentFolderId"), []).append(f["id"])

    out: list[str] = [root_id]
    seen = {root_id}
    stack = [root_id]
    while stack:
        current = stack.pop()
        for child_id in children.get(current, []):
            if child_id not in seen:
                seen.add(child_id)
                out.append(child_id)
                stack.append(child_id)
    return out


async def list_folders(project_id: str) -> list[dict]:
    return await repository.list_folders(project_id)


async def get_folder(folder_id: str) -> dict | None:
    return await repository.get_folder(folder_id)


async def create_folder(body: FolderCreateBody) -> dict:
    return await repository.create_folder(body.model_dump())


async def update_folder(folder_id: str, body: FolderRenameBody) -> dict | None:
    # exclude_unset: PATCH parcial — sólo se tocan los campos enviados.
    return await repository.update_folder(folder_id, body.model_dump(exclude_unset=True))


async def delete_folder(folder_id: str) -> bool:
    return await repository.delete_folder(folder_id)
