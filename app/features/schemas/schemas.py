"""DTOs de `schemas`."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class SchemaBody(BaseModel):
    name: str
    description: str | None = None
    # doc 44: opcional — en el PATCH, ausente significa "no tocar el vigente".
    kind: Literal["tables", "views"] | None = None
