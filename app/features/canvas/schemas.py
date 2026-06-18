"""DTOs HTTP de la feature `canvas`."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class CanvasReplaceRequest(BaseModel):
    tables: list[dict[str, Any]] | None = None
    relationships: list[dict[str, Any]] | None = None


class PositionPayload(BaseModel):
    x: float
    y: float


class PatchPositionsRequest(BaseModel):
    tables: dict[str, PositionPayload] | None = None
