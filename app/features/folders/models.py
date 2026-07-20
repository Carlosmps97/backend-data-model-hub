"""Modelos de `folders` (carpeta del Model Explorer; agrupa canvases y/o
subcarpetas dentro de un proyecto, estilo Erwin)."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.core.models import DOC_CONFIG


class FolderDoc(BaseModel):
    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    projectId: str
    parentFolderId: str | None = None
    name: str
    order: int = 0
