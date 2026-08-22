"""Modelo de la feature `settings` (configuración de naming por scope)."""
from __future__ import annotations

from pydantic import BaseModel

from app.core.models import DOC_CONFIG

# Defaults por scope (sembrados cuando el doc no existe todavía).
# Regla corporativa: join (sin separador) + UPPER en AMBOS scopes — así se
# derivan los físicos reales del catálogo (CODCLAVESUJETOCLI). La UI muestra
# las otras opciones pero deshabilitadas.
SCOPES = ("column", "table")
# `maxLength` = límite de caracteres del nombre FÍSICO (tabla/columna). Default
# 150 en ambos scopes (pedido owner). Se versiona en Data Standards.
DEFAULTS: dict[str, dict] = {
    "column": {"separator": "", "case": "upper", "maxLength": 150},
    "table": {"separator": "", "case": "upper", "maxLength": 150},
}


class NamingConfigDoc(BaseModel):
    """1 doc por scope en `naming_config`. `scope` es la clave natural (_id)."""

    model_config = DOC_CONFIG

    scope: str  # 'column' | 'table'
    separator: str = ""
    case: str = "upper"  # 'upper' | 'lower' | 'camel'
    maxLength: int = 150  # límite de caracteres del nombre físico
