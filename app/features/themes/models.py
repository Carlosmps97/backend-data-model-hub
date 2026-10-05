"""Doc 109 — themes de color del proyecto (Data Standards → Themes).

Un theme es un color con NOMBRE («Entidad Principal» = #FFFF80…) que las tablas
y vistas usan por referencia (`'theme:<id>'`): si cambia el color del theme,
cambian todas las cajas que lo usan, en todos los canvases — como los themes de
Erwin. Se versionan con los demás estándares del proyecto (apply / rollback).
"""
from __future__ import annotations

import re
import uuid

from pydantic import BaseModel, Field, field_validator

from app.core.models import DOC_CONFIG

MAX_NAME_LEN = 80
_HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def theme_name_error(name: object) -> str | None:
    """Mensaje si el nombre no sirve; None si sirve. Puro."""
    if not isinstance(name, str) or not name.strip():
        return "Every theme needs a name."
    if len(name.strip()) > MAX_NAME_LEN:
        return f"Theme names can have at most {MAX_NAME_LEN} characters."
    return None


def theme_color_error(color: object) -> str | None:
    if not isinstance(color, str) or not _HEX.match(color.strip()):
        return f"'{color}' is not a color: use #RRGGBB."
    return None


class ThemeDoc(BaseModel):
    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    projectId: str
    name: str
    color: str                 # '#RRGGBB'
    order: int = 0             # orden en el selector de color

    @field_validator("color", mode="before")
    @classmethod
    def _upper(cls, v: object) -> object:
        return v.strip().upper() if isinstance(v, str) else v
