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
SHEET_NAME_RULE = "The sheet name must be 1–31 characters, without : \\ / ? * [ ]."

# Doc 102: nombre del archivo del export. Los marcadores los resuelve el front
# con la hora LOCAL de quien exporta; acá solo la FORMA (espejo de
# `fileNameProblem` en `features/reporting/lib/sheetTemplates.ts`).
FILE_TOKENS = ("yyyy", "MM", "dd", "HH", "mm", "ss")
FILE_NAME_MAX = 120
_TOKEN = re.compile(r"\{([^{}]*)\}")
_BAD_FILE = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


def clean_file_name(value: str | None) -> str | None:
    """Nombre guardado: sin blancos alrededor ni `.xlsx` final; vacío ⇒ None.
    Levanta ValueError (texto en inglés: llega al 422) si la forma no sirve. Puro."""
    text = (value or "").strip()
    if text.lower().endswith(".xlsx"):
        text = text[:-5].rstrip()
    if not text:
        return None
    if len(text) > FILE_NAME_MAX:
        raise ValueError(f"The file name can have at most {FILE_NAME_MAX} characters.")
    if _BAD_FILE.search(text):
        raise ValueError('The file name can\'t contain \\ / : * ? " < > |.')
    unknown = next((t for t in _TOKEN.findall(text) if t not in FILE_TOKENS), None)
    if unknown is not None:
        raise ValueError("Unknown placeholder {" + unknown + "} in the file name — "
                         "use {yyyy} {MM} {dd} {HH} {mm} {ss}.")
    rest = _TOKEN.sub("", text)
    if "{" in rest or "}" in rest:
        raise ValueError("Unbalanced braces in the file name — placeholders look like {yyyy}.")
    return text


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
    # Doc 102: nombre del archivo con marcadores de fecha/hora (hora local del
    # navegador); None = `<plantilla>-<fecha>.xlsx`.
    fileName: str | None = Field(default=None, max_length=200)

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
            raise ValueError(SHEET_NAME_RULE)
        return v

    @field_validator("sheetName")
    @classmethod
    def _sheet_fits_excel(cls, v: str) -> str:
        # Doc 105: el tope de 31 de Excel (y de SheetJS, que escribe el export)
        # cuenta unidades UTF-16 — igual que el front; `len()` contaba code
        # points y dejaba pasar nombres que el export no podía escribir. Sólo al
        # ESCRIBIR: `SheetTemplateDoc` lo anula (ver ahí).
        if len(v.encode("utf-16-le")) // 2 > 31:
            raise ValueError(SHEET_NAME_RULE)
        return v

    @field_validator("fileName")
    @classmethod
    def _check_file_name(cls, v: str | None) -> str | None:
        return clean_file_name(v)

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

    @field_validator("sheetName")
    @classmethod
    def _sheet_fits_excel(cls, v: str) -> str:
        """Doc 105 (revisión, hallazgo 4): al LEER no rige el tope UTF-16 — una
        plantilla guardada antes (≤31 code points, p. ej. con emoji) dejaba en
        500 el listado de TODO el proyecto y ni siquiera se podía abrir para
        corregirla. Editarla sí lo exige (`SheetTemplateBody`)."""
        return v
