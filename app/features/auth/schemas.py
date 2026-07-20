"""DTOs de la feature `auth`."""
from __future__ import annotations

from pydantic import BaseModel


class LoginBody(BaseModel):
    username: str
    password: str
