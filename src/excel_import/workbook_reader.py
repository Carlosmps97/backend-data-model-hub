"""Lectura de un workbook xlsx en estructuras simples (sin lógica de negocio).

Responsabilidades:
- Abrir el archivo con `openpyxl` en modo read-only (no requiere mantener
  todo el workbook editable en memoria — relevante para archivos grandes).
- Para cada hoja, devolver:
    * el nombre original de la hoja (`Customer`, `dbo.Customer`, ...),
    * las filas con valores ya en strings y trim aplicado.
- Identificar la hoja especial `TablesDescriptions` (case-insensitive) y
  separarla del resto para que `service.py` la procese aparte.

Decisiones de diseño:
- La primera fila se asume *siempre* header — el contrato con el usuario
  es explícito (cabecera obligatoria). Esto evita la heurística
  "detectar si es header" que tenía el parser viejo y que fallaba si el
  usuario nombraba la primer columna distinto a `name`.
- No se rompe el contrato si una hoja tiene celdas vacías intercaladas
  con datos: paramos cuando todas las columnas relevantes están vacías
  y descartamos filas sin nombre de columna.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import IO

from openpyxl import load_workbook


#: Nombre reservado para la hoja de descripciones de tablas. Match
#: case-insensitive — `tablesdescriptions`, `TablesDescriptions`, etc.
TABLE_DESCRIPTIONS_SHEET = "tablesdescriptions"


@dataclass(slots=True)
class RawColumnRow:
    """Una fila de columna leída del Excel (sin normalizar tipos)."""

    name: str
    data_type: str
    functional_definition: str | None


@dataclass(slots=True)
class RawSheet:
    """Una hoja del workbook representando una tabla."""

    sheet_name: str  # nombre original, p.ej. "dbo.Customer"
    columns: list[RawColumnRow] = field(default_factory=list)


@dataclass(slots=True)
class RawWorkbook:
    """Resultado crudo de leer un workbook."""

    sheets: list[RawSheet]
    #: `{<sheet name original>: <descripción>}`. Las llaves vienen tal cual del
    #: usuario (con o sin schema). El service hace match flexible.
    table_descriptions: dict[str, str]
    warnings: list[str]


# ─── API pública ───────────────────────────────────────────────────────────


def read_workbook(file_stream: IO[bytes]) -> RawWorkbook:
    """Lee un xlsx y devuelve `RawWorkbook`.

    No realiza ninguna interpretación semántica (eso lo hace `service`).
    El stream debe estar posicionado al inicio.
    """
    workbook = load_workbook(filename=file_stream, read_only=True, data_only=True)
    sheets: list[RawSheet] = []
    table_descriptions: dict[str, str] = {}
    warnings: list[str] = []

    try:
        for sheet_name in workbook.sheetnames:
            worksheet = workbook[sheet_name]
            if sheet_name.strip().lower() == TABLE_DESCRIPTIONS_SHEET:
                table_descriptions.update(_read_descriptions_sheet(worksheet, warnings))
                continue

            sheet = _read_table_sheet(sheet_name, worksheet, warnings)
            if sheet is not None:
                sheets.append(sheet)
    finally:
        workbook.close()

    return RawWorkbook(
        sheets=sheets,
        table_descriptions=table_descriptions,
        warnings=warnings,
    )


# ─── Lectura de cada tipo de hoja ──────────────────────────────────────────


def _read_table_sheet(
    sheet_name: str,
    worksheet,  # openpyxl.worksheet.worksheet.Worksheet — sin tipar para evitar import pesado
    warnings: list[str],
) -> RawSheet | None:
    """Lee una hoja de tabla. La primera fila es header (se descarta).

    Asume el contrato: columna A=nombre, B=tipo de dato, C=descripción
    funcional (opcional).
    """
    rows_iter = worksheet.iter_rows(values_only=True)
    header = next(rows_iter, None)
    if header is None:
        warnings.append(f'Sheet "{sheet_name}" is empty.')
        return None

    columns: list[RawColumnRow] = []
    for row in rows_iter:
        name = _cell_str(row, 0)
        if not name:
            # Sin nombre de columna no tiene sentido la fila; la saltamos.
            # Esto cubre filas vacías intercaladas o filas con solo definición.
            continue
        columns.append(
            RawColumnRow(
                name=name,
                data_type=_cell_str(row, 1),
                functional_definition=_cell_str(row, 2) or None,
            )
        )

    if not columns:
        warnings.append(f'Sheet "{sheet_name}" has a header but no column rows.')
        return None

    return RawSheet(sheet_name=sheet_name, columns=columns)


def _read_descriptions_sheet(worksheet, warnings: list[str]) -> dict[str, str]:
    """Lee la hoja `TablesDescriptions`.

    Espera dos columnas: `TableName` y `TableDescription`. La primera fila
    es header. La búsqueda de las columnas es por *posición* (A y B) — un
    contrato simple y predecible. Si el usuario rota el header, igual
    devolvemos lo que esté en A/B y agregamos un warning.
    """
    rows_iter = worksheet.iter_rows(values_only=True)
    header = next(rows_iter, None)
    if header is None:
        return {}

    header_a = _cell_str(header, 0).lower()
    header_b = _cell_str(header, 1).lower()
    if "table" not in header_a or "name" not in header_a:
        warnings.append(
            'TablesDescriptions sheet: column A should be "TableName" '
            f'(got "{_cell_str(header, 0)}"). Reading by position anyway.'
        )
    if "desc" not in header_b:
        warnings.append(
            'TablesDescriptions sheet: column B should be "TableDescription" '
            f'(got "{_cell_str(header, 1)}"). Reading by position anyway.'
        )

    out: dict[str, str] = {}
    for row in rows_iter:
        table_name = _cell_str(row, 0)
        description = _cell_str(row, 1)
        if not table_name or not description:
            continue
        out[table_name] = description
    return out


# ─── Helpers ───────────────────────────────────────────────────────────────


def _cell_str(row: tuple, index: int) -> str:
    """Lee la celda `index` como string limpio.

    Convierte `None` a `""`, hace `str()` para soportar números/fechas y
    aplica `strip()`. Mantener esto centralizado evita inconsistencias
    al consumir filas en distintos puntos.
    """
    if row is None or index >= len(row):
        return ""
    value = row[index]
    if value is None:
        return ""
    return str(value).strip()
