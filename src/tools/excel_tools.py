"""Herramientas para procesamiento de archivos Excel (.xlsx).

Parsea archivos de input del usuario donde cada pestaña representa
una tabla del modelo de datos.
"""

import json
import re
from pathlib import Path
from typing import Annotated

from agent_framework import tool
from pydantic import Field


def _normalize_header(header: str | None) -> str:
    """Normaliza un encabezado de columna del Excel para matching flexible."""
    if header is None:
        return ""
    h = str(header).strip().lower()
    h = re.sub(r"[\s_\-]+", "_", h)
    return h


# Mapeo de posibles nombres de columna del Excel a nombres internos
_HEADER_ALIASES = {
    "column_name": ["column_name", "columna", "nombre", "nombre_columna", "col_name", "field", "campo"],
    "functional_definition": [
        "functional_definition",
        "definicion",
        "definicion_funcional",
        "definition",
        "descripcion",
        "description",
        "desc",
        "funcional",
    ],
    "data_type_hint": [
        "data_type_hint",
        "data_type",
        "tipo",
        "tipo_dato",
        "type",
        "type_hint",
        "datatype",
    ],
    "is_nullable": ["is_nullable", "nullable", "nulo", "permite_nulos", "null", "nulable"],
    "notes": ["notes", "notas", "observaciones", "comentarios", "comments", "note"],
}


def _match_header(raw_header: str) -> str | None:
    """Intenta mapear un encabezado del Excel a un campo interno."""
    normalized = _normalize_header(raw_header)
    for field_name, aliases in _HEADER_ALIASES.items():
        if normalized in aliases:
            return field_name
    return None


@tool(approval_mode="never_require")
def parse_excel_file(
    file_path: Annotated[
        str,
        Field(description="Ruta absoluta o relativa al archivo .xlsx a procesar"),
    ],
) -> str:
    """Parsea un archivo Excel donde cada pestaña es una tabla del modelo de datos.

    Retorna un JSON con la estructura de tablas y columnas encontradas.
    Robusto ante variaciones en nombres de columnas (case-insensitive, con/sin espacios).
    """
    path = Path(file_path)
    if not path.exists():
        return json.dumps({"error": f"Archivo no encontrado: {file_path}"}, ensure_ascii=False)

    if path.suffix.lower() != ".xlsx":
        return json.dumps({"error": f"Formato no soportado: {path.suffix}. Solo .xlsx"}, ensure_ascii=False)

    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    tables = []

    for sheet_name in wb.sheetnames:
        ws = wb[sheet_name]
        rows = list(ws.iter_rows(values_only=True))

        if not rows:
            continue

        # Mapear headers
        raw_headers = rows[0]
        header_map: dict[int, str] = {}
        for idx, h in enumerate(raw_headers):
            if h is not None:
                matched = _match_header(str(h))
                if matched:
                    header_map[idx] = matched

        # Si no se encontró functional_definition, intentar por posición
        has_definition = "functional_definition" in header_map.values()
        if not has_definition and len(raw_headers) >= 2:
            # Asumir que la segunda columna es la definición funcional
            header_map[1] = "functional_definition"
            if 0 not in header_map:
                header_map[0] = "column_name"

        columns = []
        for row in rows[1:]:
            if not any(cell is not None and str(cell).strip() for cell in row):
                continue

            col_data: dict = {}
            for idx, field_name in header_map.items():
                if idx < len(row):
                    value = row[idx]
                    if value is not None:
                        if field_name == "is_nullable":
                            val_str = str(value).strip().lower()
                            col_data[field_name] = val_str in ("true", "1", "si", "sí", "yes", "y", "s")
                        else:
                            col_data[field_name] = str(value).strip()

            # Solo agregar si tiene al menos la definición funcional
            if col_data.get("functional_definition"):
                columns.append(col_data)

        if columns:
            tables.append({"table_name": sheet_name.strip(), "columns": columns})

    wb.close()

    if not tables:
        return json.dumps(
            {"error": "No se encontraron tablas válidas en el archivo Excel."},
            ensure_ascii=False,
        )

    return json.dumps(
        {"tables": tables, "total_tables": len(tables)},
        indent=2,
        ensure_ascii=False,
    )
