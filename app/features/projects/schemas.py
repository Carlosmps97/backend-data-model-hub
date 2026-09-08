"""DTOs de `projects` / `subject_areas`."""
from __future__ import annotations

from pydantic import BaseModel

from app.features.data_standards.schemas import CopyFromBody


class ProjectCreateBody(BaseModel):
    """Doc 75 D5/D15: la creación es la ÚNICA operación directa sobre un proyecto
    (renombrar/describir/borrar van por draft). `copyFrom` copia bloques de
    Data Standards de otro proyecto; sin él, el proyecto nace vacío."""
    name: str
    description: str | None = None
    copyFrom: CopyFromBody | None = None


class SubjectAreaBody(BaseModel):
    projectId: str
    name: str
    folderId: str | None = None  # ubica el canvas en el Model Explorer (aditivo)


class TablesBody(BaseModel):
    tableIds: list[str]


class ViewsBody(BaseModel):
    """Doc 70: membresía de vistas del canvas (lista COMPLETA, reemplaza)."""
    viewIds: list[str]


class LayoutBody(BaseModel):
    layout: dict  # { tableId: {x, y} }


class DrawingsBody(BaseModel):
    drawings: list[dict]  # capa DRAWING (debajo de los nodos): formas/texto con estilo + posición
