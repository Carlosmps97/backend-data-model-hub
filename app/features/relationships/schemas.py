"""DTOs de `relationships` (v2: parent/child + pares, doc 19)."""
from __future__ import annotations

from pydantic import BaseModel, Field


class RelationshipPairBody(BaseModel):
    parentColumnId: str
    childColumnId: str
    roleName: str | None = None


class RelationshipBody(BaseModel):
    parentTableId: str
    childTableId: str
    pairs: list[RelationshipPairBody] = Field(min_length=1)
    parentCardinality: str = "one"
    childCardinality: str = "zero-many"
    identifying: bool = False
    subcategory: bool = False
    subtypeSymbolId: str | None = None
