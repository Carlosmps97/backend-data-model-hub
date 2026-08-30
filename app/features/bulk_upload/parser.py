"""Parser del workbook (doc 55 §4): del JSON por hoja a filas tipadas. Puro.

Cabeceras tolerantes: caso, espacios, guiones y tildes no importan
(`tabla lógico` ≡ `TABLA_LOGICO`). Las cabeceras `UDP_*` se conservan con su
texto original — el planner las resuelve contra las definiciones vivas. Las
cabeceras desconocidas se ignoran con warning; las obligatorias ausentes
invalidan la hoja (error). Las filas totalmente vacías se saltan.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .normalize import clean_text, is_pk_mark, strip_accents
from .report import SHEET_COLUMNS, SHEET_TABLES, SHEET_WORKBOOK, Issue
from .schemas import Sheet, UploadWorkbookBody

# Cabecera de la plantilla → atributo de la fila tipada.
TABLE_HEADERS: dict[str, str] = {
    "PROJECT": "project", "SPACE": "space", "SUBJECT": "subject", "DIAGRAMA": "diagram",
    "ESQUEMA": "schema", "TABLA_LOGICO": "logical", "TABLA_FISICA": "physical",
    "DEF_TABLA": "description",
}
COLUMN_HEADERS: dict[str, str] = {
    "TABLA_LOGICO": "table_logical", "CAMPO_LOGICO": "logical", "CAMPO_FISICO": "physical",
    "DEF_ATRIBUTO": "description", "PARENT_DOMAIN": "domain", "TIPO_DATO": "data_type",
    "PK": "pk",
}
REQUIRED_TABLE_HEADERS = ("TABLA_LOGICO",)
REQUIRED_COLUMN_HEADERS = ("TABLA_LOGICO", "CAMPO_LOGICO")
UDP_PREFIX = "UDP_"


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
    udp: dict[str, str] = field(default_factory=dict)   # cabecera original → valor


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


@dataclass
class ParsedWorkbook:
    tables: list[TableRow] = field(default_factory=list)
    columns: list[ColumnRow] = field(default_factory=list)
    table_udp_headers: list[str] = field(default_factory=list)
    column_udp_headers: list[str] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)
    has_tables_sheet: bool = False
    has_columns_sheet: bool = False


def header_key(header) -> str:
    """`tabla lógico` → `TABLA_LOGICO` (sin tildes, MAYÚSCULAS, separadores → `_`)."""
    text = strip_accents(clean_text(header)).upper()
    return re.sub(r"[\s\-]+", "_", text).strip("_")


@dataclass
class _HeaderMap:
    known: dict[str, str]        # cabecera original → atributo
    udp: list[str]               # cabeceras UDP originales (en orden)


def _map_headers(sheet: Sheet, sheet_name: str, known: dict[str, str],
                 required: tuple[str, ...], issues: list[Issue]) -> _HeaderMap | None:
    out = _HeaderMap(known={}, udp=[])
    seen: set[str] = set()
    for h in sheet.headers:
        key = header_key(h)
        if not key or key in seen:
            continue
        seen.add(key)
        if key in known:
            out.known[h] = known[key]
        elif key.startswith(UDP_PREFIX):
            out.udp.append(h)
        else:
            issues.append(Issue("warning", sheet_name, None, h, "unknown-header",
                                f"Column '{h}' is not part of the template and was ignored."))
    missing = [r for r in required if r not in seen]
    if missing:
        for r in missing:
            issues.append(Issue("error", sheet_name, None, r, "missing-header",
                                f"Sheet '{sheet_name}' is missing the required column '{r}'."))
        return None
    return out


def _is_blank(cells: dict[str, str]) -> bool:
    return all(not clean_text(v) for v in cells.values())


def _parse_tables(sheet: Sheet, issues: list[Issue]) -> tuple[list[TableRow], list[str]]:
    hm = _map_headers(sheet, SHEET_TABLES, TABLE_HEADERS, REQUIRED_TABLE_HEADERS, issues)
    if hm is None:
        return [], []
    rows: list[TableRow] = []
    for r in sheet.rows:
        if _is_blank(r.cells):
            continue
        t = TableRow(row=r.row)
        for header, attr in hm.known.items():
            setattr(t, attr, clean_text(r.cells.get(header)))
        t.udp = {h: clean_text(r.cells.get(h)) for h in hm.udp}
        rows.append(t)
    return rows, hm.udp


def _parse_columns(sheet: Sheet, issues: list[Issue]) -> tuple[list[ColumnRow], list[str]]:
    hm = _map_headers(sheet, SHEET_COLUMNS, COLUMN_HEADERS, REQUIRED_COLUMN_HEADERS, issues)
    if hm is None:
        return [], []
    rows: list[ColumnRow] = []
    for r in sheet.rows:
        if _is_blank(r.cells):
            continue
        c = ColumnRow(row=r.row)
        for header, attr in hm.known.items():
            value = r.cells.get(header)
            if attr == "pk":
                c.pk = is_pk_mark(value)
            else:
                setattr(c, attr, clean_text(value))
        c.udp = {h: clean_text(r.cells.get(h)) for h in hm.udp}
        rows.append(c)
    return rows, hm.udp


def parse_workbook(body: UploadWorkbookBody) -> ParsedWorkbook:
    """Filas tipadas + cabeceras UDP + issues de estructura del workbook."""
    parsed = ParsedWorkbook(has_tables_sheet=body.sheets.tables is not None,
                            has_columns_sheet=body.sheets.columns is not None)
    if not parsed.has_tables_sheet and not parsed.has_columns_sheet:
        parsed.issues.append(Issue("error", SHEET_WORKBOOK, None, None, "missing-sheet",
                                   "The workbook has neither a 'Tablas' nor an 'Atributos' sheet."))
        return parsed
    if body.sheets.tables is not None:
        parsed.tables, parsed.table_udp_headers = _parse_tables(body.sheets.tables, parsed.issues)
    if body.sheets.columns is not None:
        parsed.columns, parsed.column_udp_headers = _parse_columns(body.sheets.columns, parsed.issues)
    if not parsed.tables and not parsed.columns and not any(i.severity == "error" for i in parsed.issues):
        parsed.issues.append(Issue("error", SHEET_WORKBOOK, None, None, "empty-workbook",
                                   "The workbook has no data rows in 'Tablas' or 'Atributos'."))
    return parsed
