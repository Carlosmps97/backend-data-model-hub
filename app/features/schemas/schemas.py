"""DTOs de `schemas`."""
from __future__ import annotations

from pydantic import BaseModel


class SchemaBody(BaseModel):
    name: str
    description: str | None = None
