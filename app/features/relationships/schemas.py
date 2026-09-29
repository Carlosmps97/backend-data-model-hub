"""DTOs de `relationships` (v2: parent/child + pares, doc 19)."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from .models import PHRASE_FIELDS, clean_phrase


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
    # Doc 98: frases de relación. Se normalizan ACÁ también: el PUT persiste
    # el dump de este DTO con un $set, sin pasar por RelationshipDoc.
    parentToChildPhrase: str | None = None
    childToParentPhrase: str | None = None

    _clean_phrases = field_validator(*PHRASE_FIELDS, mode="before")(clean_phrase)
