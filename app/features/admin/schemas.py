"""DTOs del módulo Admin (usuarios + roles)."""
from __future__ import annotations

from pydantic import BaseModel


class UserCreate(BaseModel):
    username: str
    email: str
    name: str
    role: str
    password: str
    projectIds: list[str] = []
    status: str = "active"


class UserUpdate(BaseModel):
    """Campos opcionales — solo se aplican los enviados. `password` opcional
    (reset por el admin)."""
    email: str | None = None
    name: str | None = None
    role: str | None = None
    projectIds: list[str] | None = None
    status: str | None = None
    password: str | None = None


class RoleBody(BaseModel):
    name: str
    description: str | None = None
    permissions: dict[str, bool] = {}
