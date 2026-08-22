"""Seam de identidad: deriva el usuario en sesión de la request.

Modo conmutable por `settings.AUTH_MODE` ("local" | "databricks"). Las features
consumen `current_principal` como dependencia FastAPI; nadie instancia los
providers a mano.
"""
from .dependencies import current_principal, get_identity_provider
from .models import Principal
from .provider import (
    DatabricksIdentityProvider,
    IdentityProvider,
    LocalIdentityProvider,
)

__all__ = [
    "Principal",
    "current_principal",
    "get_identity_provider",
    "IdentityProvider",
    "LocalIdentityProvider",
    "DatabricksIdentityProvider",
]
