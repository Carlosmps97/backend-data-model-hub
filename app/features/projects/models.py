"""Modelos de `projects` (Project = contenedor padre; Subject Area = módulo/pestaña
que referencia un subconjunto del pool de tablas canónicas + su layout)."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.core.models import DOC_CONFIG


class ProjectDoc(BaseModel):
    """Raíz del alcance (doc 75): no lleva `projectId`. Se crea directo; se
    renombra/describe/borra por draft del propio proyecto (D5)."""
    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str | None = None


class NodePosDoc(BaseModel):
    model_config = DOC_CONFIG
    x: float
    y: float


class SubjectAreaDoc(BaseModel):
    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    projectId: str
    # `folderId` ubica el canvas dentro de la jerarquía del Model Explorer
    # (None = raíz del proyecto). `drawings` = capa DRAWING (shapes/text con
    # estilo), un canvas = un diagrama ER. Ambos aditivos (invariante §2.6).
    folderId: str | None = None
    name: str
    tableIds: list[str] = Field(default_factory=list)
    layout: dict[str, NodePosDoc] = Field(default_factory=dict)
    drawings: list[dict] = Field(default_factory=list)
    # F5 — UDP del Modelo de Datos: valores {defId: value} de las definiciones
    # level='canvas'. Aditivo con default (invariante §2.6: declarado acá Y en
    # el TS SubjectArea, o el dato desaparece al recargar).
    udpValues: dict[str, str] = Field(default_factory=dict)
    # Doc 70 §2: membresía EXPLÍCITA de vistas en el canvas (paridad con
    # `tableIds`; la posición ya vivía en `layout[viewId]`). None = canvas
    # LEGACY que nunca materializó su lista: rige la regla vieja del doc 10 D3
    # (`showOnCanvas` ∧ fuentes ∩ tableIds). Lista (aun vacía) = sólo esas
    # vistas. Aditivo (invariante §2.6): declarado acá Y en el TS SubjectArea.
    viewIds: list[str] | None = None
