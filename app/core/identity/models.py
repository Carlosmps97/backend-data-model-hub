"""Identidad del usuario en sesión (seam de auth).

`source` distingue el origen: "local" (usuario fake de desarrollo) o
"databricks" (reenviado por el proxy SSO de Databricks Apps).
"""
from __future__ import annotations

from pydantic import BaseModel

from app.core.models import DOC_CONFIG


class Principal(BaseModel):
    model_config = DOC_CONFIG

    email: str
    username: str
    display_name: str
    source: str  # "local" | "databricks"
