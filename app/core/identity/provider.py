"""Implementaciones del puerto de identidad.

- `LocalIdentityProvider`: usuario fake fijo (desarrollo sin Databricks).
- `DatabricksIdentityProvider`: lee los headers que el proxy SSO de Databricks
  Apps reenvía en cada request.
"""
from __future__ import annotations

from typing import Protocol

from starlette.requests import Request

from .models import Principal


class IdentityProvider(Protocol):
    """Puerto: deriva el `Principal` de la request entrante."""

    def principal_from_request(self, request: Request) -> Principal: ...


class LocalIdentityProvider:
    """Devuelve siempre el mismo usuario fake (modo `local`)."""

    def __init__(
        self,
        email: str,
        username: str | None = None,
        display_name: str | None = None,
    ) -> None:
        self._email = email
        self._username = username or email.split("@", 1)[0]
        self._display_name = display_name or self._username

    def principal_from_request(self, request: Request) -> Principal:
        # En dev, X-Dev-User permite actuar como otro usuario (probar aprobación)
        # sin reiniciar el server.
        dev_user = request.headers.get("X-Dev-User")
        if dev_user:
            return Principal(email=f"{dev_user}@local", username=dev_user,
                             display_name=dev_user, source="local")
        return Principal(
            email=self._email,
            username=self._username,
            display_name=self._display_name,
            source="local",
        )


class DatabricksIdentityProvider:
    """Lee la identidad reenviada por el proxy SSO (modo `databricks`)."""

    def principal_from_request(self, request: Request) -> Principal:
        h = request.headers
        email = h.get("X-Forwarded-Email", "")
        email_prefix = email.split("@", 1)[0] if email else ""
        username = h.get("X-Forwarded-Preferred-Username") or email_prefix
        display_name = h.get("X-Forwarded-User") or username
        return Principal(
            email=email,
            username=username,
            display_name=display_name,
            source="databricks",
        )
