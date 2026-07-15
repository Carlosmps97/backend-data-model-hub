"""DTOs de `settings` (naming_config)."""
from __future__ import annotations

from pydantic import BaseModel


class NamingConfigBody(BaseModel):
    """Body del PUT por scope. `scope` viaja en el path, no en el body."""

    separator: str = ""  # regla corporativa: join (sin separador)
    case: str = "upper"  # 'upper' | 'lower' | 'camel'
