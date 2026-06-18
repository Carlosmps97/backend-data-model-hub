"""DTOs HTTP de la feature `projects`."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class CreateProjectRequest(BaseModel):
    name: str
    description: str | None = None
    # Jerarquía Aurora — arrays chicos embebidos. Normalmente vacíos al crear;
    # el hub del proyecto los llena luego vía PUT.
    engines: list[str] | None = None
    layers: list[dict[str, Any]] | None = None
    domains: list[dict[str, Any]] | None = None


class UpdateProjectRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    engines: list[str] | None = None
    layers: list[dict[str, Any]] | None = None
    domains: list[dict[str, Any]] | None = None
