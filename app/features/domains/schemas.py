"""DTOs de entrada de `domains`."""
from __future__ import annotations

from pydantic import BaseModel


class ParentDomainBody(BaseModel):
    name: str
    defaultDataType: str
    namingTerm: str | None = None  # R5b: aditivo (ver ParentDomainDoc).
    description: str | None = None
