"""Orquestador del flujo de importación Excel.

Toma los bytes del archivo subido y produce un `ExcelPreview` listo
para que el frontend lo renderice. Su trabajo consiste en pegar las
piezas que ya están bien aisladas en otros módulos:

    bytes
      │
      ▼
    workbook_reader.read_workbook   (parseo crudo, sin semántica)
      │
      ▼
    _split_sheet_name              ({schema}.{table})
    type_normalizer.normalize_type (tipo canónico + length/scale)
    _match_table_description       (matchea descripciones con tablas)
      │
      ▼
    ExcelPreview                   (pydantic — contrato HTTP)
"""

from __future__ import annotations

from io import BytesIO

from .schemas import ExcelPreview, PreviewColumn, PreviewTable
from .normalizer import normalize_type
from .reader import RawSheet, RawWorkbook, read_workbook


def parse_workbook(file_bytes: bytes) -> ExcelPreview:
    """Parsea los bytes del xlsx y devuelve el preview ya normalizado."""
    raw: RawWorkbook = read_workbook(BytesIO(file_bytes))

    tables = [_build_preview_table(sheet, raw.table_descriptions) for sheet in raw.sheets]

    warnings = list(raw.warnings)
    if raw.table_descriptions:
        _emit_unmatched_description_warnings(tables, raw.table_descriptions, warnings)

    return ExcelPreview(
        tables=tables,
        tableDescriptionsFound=bool(raw.table_descriptions),
        warnings=warnings,
    )


# ─── Construcción del PreviewTable ─────────────────────────────────────────


def _build_preview_table(
    sheet: RawSheet,
    descriptions: dict[str, str],
) -> PreviewTable:
    schema, table_name = _split_sheet_name(sheet.sheet_name)

    columns = [
        _normalize_column(row.name, row.data_type, row.functional_definition)
        for row in sheet.columns
    ]

    return PreviewTable(
        sheetName=sheet.sheet_name,
        schema_=schema,
        name=table_name,
        description=_match_table_description(sheet.sheet_name, schema, table_name, descriptions),
        columns=columns,
    )


def _normalize_column(name: str, raw_type: str, functional_definition: str | None) -> PreviewColumn:
    normalized = normalize_type(raw_type)
    return PreviewColumn(
        name=name,
        dataType=normalized.canonical,
        rawDataType=normalized.raw,
        length=normalized.length,
        scale=normalized.scale,
        functionalDefinition=functional_definition,
        typeConfidence=normalized.confidence,
        typeMatchedVia=normalized.matched_via,
    )


# ─── Parsing del nombre de hoja ────────────────────────────────────────────


def _split_sheet_name(sheet_name: str) -> tuple[str | None, str]:
    """Separa `{schema}.{table}` con la regla acordada.

    - `"dbo.Customer"`   → `("dbo", "Customer")`
    - `"Customer"`        → `(None, "Customer")`
    - `"a.b.c"`           → `("a", "b.c")`  *(solo el primer punto separa)*

    Si después del split el schema o el nombre quedan vacíos
    (`".Customer"`, `"dbo."`) tratamos el nombre original como el de la
    tabla sin schema — más conservador que aceptar un schema vacío.
    """
    raw = sheet_name.strip()
    if "." not in raw:
        return None, raw

    schema, _, table = raw.partition(".")
    schema = schema.strip()
    table = table.strip()
    if not schema or not table:
        return None, raw
    return schema, table


# ─── Match de descripciones de tabla ───────────────────────────────────────


def _match_table_description(
    sheet_name: str,
    schema: str | None,
    table_name: str,
    descriptions: dict[str, str],
) -> str | None:
    """Encuentra la descripción para una tabla.

    Probamos en orden de especificidad creciente (más específico primero):
      1. Match exacto con el `sheetName` original (con o sin schema).
      2. Match con `schema.table_name` reconstruido (por si el usuario tipeó
         minúsculas/mayúsculas distintas).
      3. Match con solo `table_name` (caso típico cuando la tabla viene sin
         schema en `TablesDescriptions`).

    Todas las comparaciones se hacen normalizadas (lowercase + trim).
    """
    if not descriptions:
        return None

    normalized = {key.strip().lower(): value for key, value in descriptions.items()}

    candidates: list[str] = [sheet_name.lower()]
    if schema:
        candidates.append(f"{schema}.{table_name}".lower())
    candidates.append(table_name.lower())

    for candidate in candidates:
        if candidate in normalized:
            return normalized[candidate]
    return None


def _emit_unmatched_description_warnings(
    tables: list[PreviewTable],
    descriptions: dict[str, str],
    warnings: list[str],
) -> None:
    """Avisa al usuario sobre descripciones que no se pudieron asociar.

    Útil cuando alguien escribe `TableName` con typo en la hoja
    `TablesDescriptions` — sin este aviso la descripción se perdería sin
    rastro.
    """
    used_keys: set[str] = set()
    for table in tables:
        for key in (
            table.sheetName.lower(),
            (f"{table.schema_}.{table.name}".lower() if table.schema_ else table.name.lower()),
            table.name.lower(),
        ):
            if key in {k.strip().lower() for k in descriptions}:
                used_keys.add(key)

    for key in descriptions:
        if key.strip().lower() not in used_keys:
            warnings.append(
                f'TablesDescriptions row "{key}" did not match any sheet — description ignored.'
            )
