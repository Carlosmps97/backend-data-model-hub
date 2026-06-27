"""Modelo de la feature `dictionary` (diccionario de abreviaturas, el "UDP")."""
from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from app.core.models import DOC_CONFIG


class AbbreviationDoc(BaseModel):
    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    term: str
    abbrev: str
    # R1c: el diccionario se parte por scope (UDP per-column vs per-table) y cada
    # término puede tipificarse (prime/class/modifier) para los badges del UDP.
    # Default 'column' = compat con docs/clientes previos a R1c.
    scope: str = "column"  # 'column' | 'table'
    wordType: str | None = None  # 'prime' | 'class' | 'modifier'
