"""DTOs de `folders`."""
from __future__ import annotations

from pydantic import BaseModel


class FolderCreateBody(BaseModel):
    projectId: str
    name: str
    parentFolderId: str | None = None
    order: int = 0


class FolderRenameBody(BaseModel):
    """Cuerpo del PATCH de carpeta. Todos opcionales: sólo se aplican los
    campos presentes (rename, mover de padre, reordenar)."""

    name: str | None = None
    parentFolderId: str | None = None
    order: int | None = None
