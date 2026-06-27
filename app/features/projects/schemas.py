"""DTOs de `projects` / `subject_areas`."""
from __future__ import annotations

from pydantic import BaseModel


class ProjectBody(BaseModel):
    name: str
    description: str | None = None


class SubjectAreaBody(BaseModel):
    projectId: str
    name: str
    folderId: str | None = None  # ubica el canvas en el Model Explorer (aditivo)


class TablesBody(BaseModel):
    tableIds: list[str]


class LayoutBody(BaseModel):
    layout: dict  # { tableId: {x, y} }


class DrawingsBody(BaseModel):
    drawings: list[dict]  # capa DRAWING (debajo de los nodos): formas/texto con estilo + posición
