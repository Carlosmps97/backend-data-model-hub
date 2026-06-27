"""Modelo de `views` (vistas SQL)."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.core.models import DOC_CONFIG


class ViewDoc(BaseModel):
    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    sql: str = ""
    description: str | None = None
    # Aditivos del editor de vista (07b-2). Todos opcionales con default
    # NO-breaking (invariante §2.6: declarados ⇒ persisten en el round-trip).
    tableId: str | None = None
    # `schema` es palabra reservada de BaseModel: se persiste/expone como
    # `schema` vía alias (mismo patrón que `catalog`), populate_by_name acepta
    # ambos nombres en la entrada.
    sql_schema: str | None = Field(default=None, alias="schema")
    tags: list[str] = []
    filter: str | None = None
    # Cada source: {table, outputAlias?, expression?}.
    sources: list[dict] = []
    outputAlias: str | None = None
    expression: str | None = None
