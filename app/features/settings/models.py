"""Modelo de la feature `settings` (configuración de naming por scope)."""
from __future__ import annotations

from pydantic import BaseModel

from app.core.models import DOC_CONFIG

# Defaults por scope (sembrados cuando el doc no existe todavía).
SCOPES = ("column", "table")
DEFAULTS: dict[str, dict[str, str]] = {
    "column": {"separator": "_", "case": "upper"},
    "table": {"separator": "", "case": "upper"},
}


class NamingConfigDoc(BaseModel):
    """1 doc por scope en `naming_config`. `scope` es la clave natural (_id)."""

    model_config = DOC_CONFIG

    scope: str  # 'column' | 'table'
    separator: str = "_"
    case: str = "upper"  # 'upper' | 'lower' | 'camel'
