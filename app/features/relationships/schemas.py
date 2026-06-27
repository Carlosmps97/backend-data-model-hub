"""DTOs de `relationships`."""
from __future__ import annotations

from pydantic import BaseModel


class RelationshipBody(BaseModel):
    sourceTableId: str
    sourceColumnId: str
    targetTableId: str
    targetColumnId: str
    sourceCardinality: str = "one"
    targetCardinality: str = "many"
    identifying: bool = False
