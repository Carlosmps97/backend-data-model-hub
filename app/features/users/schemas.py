"""DTOs HTTP de la feature `users` (gestión de usuarios por admin)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class PermissionPayload(BaseModel):
    # Grant a nivel proyecto. Campos legacy `scope`/`modelId` de clientes
    # viejos se ignoran silenciosamente (Pydantic v2 `extra="ignore"` por def.).
    projectId: str
    level: str  # "view" | "edit"


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str  # "admin" | "editor" | "viewer"
    isActive: bool = True
    permissions: list[PermissionPayload] = Field(default_factory=list)


class UpdateUserRequest(BaseModel):
    username: str | None = None
    password: str | None = None
    role: str | None = None
    isActive: bool | None = None
    permissions: list[PermissionPayload] | None = None
