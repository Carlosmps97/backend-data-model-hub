"""Modelo de `relationships` (ER: PK/FK entre tablas canónicas)."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.core.models import DOC_CONFIG


class RelationshipDoc(BaseModel):
    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    sourceTableId: str
    sourceColumnId: str
    targetTableId: str
    targetColumnId: str
    sourceCardinality: str = "one"   # one | many | one-only | zero-one | one-many | zero-many
    targetCardinality: str = "many"
    identifying: bool = False  # True = la FK migra a la PK del hijo (línea sólida)
