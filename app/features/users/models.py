"""Modelos Pydantic de la feature `users` (cuentas + permisos por proyecto)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from app.core.models import DOC_CONFIG


class PermissionDoc(BaseModel):
    """Grant a nivel proyecto. Los campos legacy `scope`/`modelId` (model-scope)
    se descartan — `extra="ignore"` mantiene los docs pre-migración cargando."""

    model_config = DOC_CONFIG

    projectId: str
    level: Literal["view", "edit"]


class UserDoc(BaseModel):
    model_config = DOC_CONFIG

    id: str
    username: str
    passwordHash: str
    role: Literal["admin", "editor", "viewer"]
    isActive: bool
    permissions: list[PermissionDoc] = Field(default_factory=list)
    createdAt: str
    statusUpdatedAt: str
    lastLoginAt: str | None = None
