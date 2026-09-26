"""Plantillas de hoja Excel del Reporting (doc 95 D11): un formato FIJO que se
guarda como dato — nombre de hoja + columnas (encabezado → dato del modelo) —,
por proyecto. El front arma las filas con las mismas funciones puras del export
tabular; acá solo se valida la FORMA y se guarda. Colección `sheet_templates`."""
from __future__ import annotations

import re

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.models import DOC_CONFIG

# `source` = `table.<campo>` | `column.<campo>` | `table.udp:<nombre>` |
# `column.udp:<nombre>`. Los campos los resuelve el front (catálogo
# `SHEET_SOURCES`); acá solo la forma, así sumar un dato nuevo no toca el backend.
SOURCE_RE = re.compile(r"^(table|column)\.(udp:.+|[A-Za-z][A-Za-z0-9]*)$")
# Excel: nombre de hoja de 1 a 31 caracteres, sin : \ / ? * [ ].
_BAD_SHEET = re.compile(r"[:\\/?*\[\]]")


class SheetTemplateColumn(BaseModel):
    header: str = Field(min_length=1, max_length=120)
    source: str

    @field_validator("header")
    @classmethod
    def _strip_header(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("The column header can't be empty.")
        return v

    @field_validator("source")
    @classmethod
    def _check_source(cls, v: str) -> str:
        v = v.strip()
        if not SOURCE_RE.match(v):
            raise ValueError(f"Unknown data source '{v}'.")
        return v


class SheetTemplateBody(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: str = Field(min_length=1, max_length=80)
    sheetName: str = Field(min_length=1, max_length=31)
    description: str | None = None
    columns: list[SheetTemplateColumn] = Field(min_length=1, max_length=200)
    # Spec D11 («dueño + compartida, como los saved reports»): la ven todos en el proyecto.
    shared: bool = False

    @field_validator("name")
    @classmethod
    def _strip_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("The template name can't be empty.")
        return v

    @field_validator("sheetName")
    @classmethod
    def _check_sheet(cls, v: str) -> str:
        v = v.strip()
        if not v or _BAD_SHEET.search(v):
            raise ValueError("The sheet name must be 1–31 characters, without : \\ / ? * [ ].")
        return v

    @model_validator(mode="after")
    def _unique_headers(self) -> "SheetTemplateBody":
        seen: set[str] = set()
        for c in self.columns:
            key = c.header.lower()
            if key in seen:
                raise ValueError(f"The header '{c.header}' is repeated.")
            seen.add(key)
        return self


class SheetTemplateDoc(SheetTemplateBody):
    model_config = DOC_CONFIG
    id: str
    projectId: str
    origin: str = "user"          # 'user' | builtin:… (sembrada)
    owner: str | None = None      # dueño (lo fija el servidor); la sembrada: `system`
    createdBy: str | None = None
    updatedBy: str | None = None
