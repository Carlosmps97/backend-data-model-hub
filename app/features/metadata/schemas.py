"""DTOs HTTP de la feature `metadata` (catálogo Semantic Types / UDPs)."""

from __future__ import annotations

from pydantic import BaseModel


class SemanticTagBody(BaseModel):
    key: str
    allowedValues: list[str] = []


class SemanticTypeBody(BaseModel):
    name: str
    dataType: str
    description: str | None = None
    tags: list[SemanticTagBody] = []


class UdpBody(BaseModel):
    name: str
    description: str | None = None
    appliesTo: list[str] = []
    valueType: str = "text"
    allowedValues: list[str] | None = None
