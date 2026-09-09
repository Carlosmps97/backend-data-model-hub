"""Filas tipadas de la carga masiva (doc 78). La INTERPRETACIÓN del workbook
(hojas, fila de cabecera, mapeo cabecera → campo/UDP) vive en
`profiles/apply.py`; acá quedan los dataclasses que consume el planner y
`header_key` (clave tolerante de cabeceras)."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .normalize import clean_text, strip_accents
from .report import Issue


@dataclass
class TableRow:
    row: int
    project: str = ""
    space: str = ""
    subject: str = ""
    diagram: str = ""
    schema: str = ""
    logical: str = ""
    physical: str = ""
    description: str = ""
    udp: dict[str, str] = field(default_factory=dict)            # cabecera → valor
    defaults: dict[str, str] = field(default_factory=dict)       # attr → default (solo entidad nueva)
    udp_defaults: dict[str, str] = field(default_factory=dict)   # cabecera → default (solo entidad nueva)


@dataclass
class ColumnRow:
    row: int
    table_logical: str = ""
    logical: str = ""
    physical: str = ""
    description: str = ""
    domain: str = ""
    data_type: str = ""
    pk: bool = False
    udp: dict[str, str] = field(default_factory=dict)
    defaults: dict[str, str] = field(default_factory=dict)
    udp_defaults: dict[str, str] = field(default_factory=dict)


@dataclass
class ParsedWorkbook:
    tables: list[TableRow] = field(default_factory=list)
    columns: list[ColumnRow] = field(default_factory=list)
    table_udp: dict[str, list[dict]] = field(default_factory=dict)     # cabecera → defs (doc 78 D2)
    column_udp: dict[str, list[dict]] = field(default_factory=dict)
    headers: dict[str, dict[str, str]] = field(default_factory=dict)   # rol → field → cabecera real
    issues: list[Issue] = field(default_factory=list)
    fatal: bool = False
    has_tables_sheet: bool = False
    has_columns_sheet: bool = False
    sheet_names: dict[str, str] = field(default_factory=dict)          # rol → nombre de hoja del perfil
    sheets_info: list[dict] = field(default_factory=list)              # [{role, name, found, headerRow, rows}]
    profile_ref: dict | None = None                                    # {id, name}

    def header(self, role: str, field_name: str, fallback: str) -> str:
        """Cabecera REAL mapeada a `field_name` (para el `column` de las
        incidencias); el fallback es el nombre de la plantilla histórica."""
        return (self.headers.get(role) or {}).get(field_name) or fallback


def header_key(header) -> str:
    """`tabla lógico` → `TABLA_LOGICO` (sin tildes, MAYÚSCULAS, separadores → `_`)."""
    text = strip_accents(clean_text(header)).upper()
    return re.sub(r"[\s\-]+", "_", text).strip("_")
