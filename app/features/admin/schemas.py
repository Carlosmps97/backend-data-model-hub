"""DTOs del módulo Admin (usuarios + roles)."""
from __future__ import annotations

from pydantic import BaseModel, Field

# Política de contraseñas: mínimo 10, máximo 128 (el tope evita entradas absurdas
# y el clip silencioso de bcrypt a 72 bytes). Se valida al CREAR/CAMBIAR (no en el
# login, que debe aceptar cualquier valor para poder verificarlo).
_PW_MIN, _PW_MAX = 10, 128


class UserCreate(BaseModel):
    """Alta en la whitelist (doc 38): lo normal es `username` = correo + `role`,
    SIN contraseña (la entrada da acceso vía SSO de Databricks). `password`
    queda para cuentas locales (username sin `@`, p.ej. `admin`) — el service
    exige una u otra forma."""
    username: str
    email: str = ""
    name: str = ""
    role: str
    password: str | None = Field(default=None, min_length=_PW_MIN, max_length=_PW_MAX)
    projectIds: list[str] = []
    status: str = "active"


class UserUpdate(BaseModel):
    """Campos opcionales — solo se aplican los enviados. `password` opcional
    (reset por el admin); si se envía, respeta la política de longitud."""
    email: str | None = None
    name: str | None = None
    role: str | None = None
    projectIds: list[str] | None = None
    status: str | None = None
    password: str | None = Field(default=None, min_length=_PW_MIN, max_length=_PW_MAX)


class RoleBody(BaseModel):
    name: str
    description: str | None = None
    permissions: dict[str, bool] = {}
