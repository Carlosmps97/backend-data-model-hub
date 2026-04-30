"""Herramientas para procesamiento de archivos Excel (.xlsx).

Parsea archivos de input del usuario donde cada pestaña representa
una tabla del modelo de datos.

Estructura esperada del Excel
─────────────────────────────
Pestañas de tablas (cualquier nombre):
  - Fila 1: encabezados (opcionales; si no se reconocen se usa posición)
  - Filas 2+: una columna por fila, con hasta 3 campos posicionales:
      • Columna A — nombre provisional de la columna (sugerencia)
      • Columna B — tipo de dato provisional (sugerencia)
      • Columna C — definición funcional (OBLIGATORIA para incluir la fila)

    Si los encabezados se reconocen (ver _HEADER_ALIASES), se usan sin
    importar la posición. Si no se reconocen, se aplica el orden posicional.

Pestaña especial "TableDefinition" (nombre flexible, ver _TABLE_DEF_ALIASES):
  - Fila 1: encabezados (opcionales)
  - Filas 2+: una fila por tabla con dos campos posicionales:
      • Columna A — nombre propuesto de la tabla (debe coincidir con el
                    nombre de la pestaña correspondiente)
      • Columna B — definición funcional / descripción de la tabla

    Esta pestaña permite que el usuario documente el propósito de cada tabla
    para que el agente genere un modelo más semántico y preciso.

IMPORTANTE: todos los nombres (de tabla y de columna) son PROVISIONALES.
El agente determina los nombres físicos finales aplicando los lineamientos
corporativos cargados (guidelines). Los tipos son solo indicaciones.
"""

import json
import re
from pathlib import Path
from typing import Annotated

from agent_framework import tool
from pydantic import Field


# ─── Nombres reconocidos para la pestaña especial de definiciones ─────────

_TABLE_DEF_ALIASES: frozenset[str] = frozenset({
    "tabledefinition",
    "table_definition",
    "table definition",
    "definicion_tablas",
    "definicion tablas",
    "definiciones de tabla",
    "definiciones",
    "table_desc",
    "tabledesc",
    "table descriptions",
    "descriptions",
})


def _is_table_def_sheet(name: str) -> bool:
    """Devuelve True si el nombre de pestaña corresponde a TableDefinition."""
    normalized = re.sub(r"[\s_\-]+", " ", name.strip().lower())
    return normalized.replace(" ", "") in _TABLE_DEF_ALIASES or normalized in _TABLE_DEF_ALIASES


# ─── Aliases de encabezados de columnas ──────────────────────────────────

_HEADER_ALIASES: dict[str, list[str]] = {
    "column_name": [
        "column_name", "columna", "nombre", "nombre_columna",
        "col_name", "field", "campo", "atributo", "attribute",
    ],
    "data_type_hint": [
        "data_type_hint", "data_type", "tipo", "tipo_dato", "tipo_de_dato",
        "type", "type_hint", "datatype", "tipo_dato_provisional",
        "tipo_provisional",
    ],
    "functional_definition": [
        "functional_definition", "definicion", "definicion_funcional",
        "definition", "descripcion", "description", "desc", "funcional",
        "definicion_funcional_columna", "definicion_columna",
    ],
    "is_nullable": [
        "is_nullable", "nullable", "nulo", "permite_nulos",
        "null", "nulable", "permite_null",
    ],
    "notes": [
        "notes", "notas", "observaciones", "comentarios",
        "comments", "note", "observacion",
    ],
}


def _normalize_header(header: str | None) -> str:
    if header is None:
        return ""
    h = str(header).strip().lower()
    # Eliminar acentos (á→a, é→e, í→i, ó→o, ú→u, ñ→n)
    h = h.translate(str.maketrans("áéíóúüñ", "aeiouun"))
    # Eliminar caracteres no alfanuméricos (paréntesis, puntos, etc.) excepto espacios y guiones
    h = re.sub(r"[^a-z0-9\s_\-]", "", h)
    # Colapsar separadores
    return re.sub(r"[\s_\-]+", "_", h).strip("_")


def _match_header(raw: str) -> str | None:
    normalized = _normalize_header(raw)
    for field_name, aliases in _HEADER_ALIASES.items():
        if normalized in aliases:
            return field_name
    return None


# ─── Detección posicional de columnas ────────────────────────────────────

def _build_positional_header_map(num_cols: int) -> dict[int, str]:
    """
    Asigna campos internos por posición cuando los encabezados no se reconocen.

    Layout estándar del Excel de usuario:
      1 col  → col0 = functional_definition
      2 cols → col0 = column_name, col1 = functional_definition
      3+ cols→ col0 = column_name, col1 = data_type_hint, col2 = functional_definition
    """
    if num_cols == 0:
        return {}
    if num_cols == 1:
        return {0: "functional_definition"}
    if num_cols == 2:
        return {0: "column_name", 1: "functional_definition"}
    # 3 o más: formato nombre | tipo | definición
    return {0: "column_name", 1: "data_type_hint", 2: "functional_definition"}


# ─── Parser de pestaña TableDefinition ───────────────────────────────────

def _parse_table_def_sheet(ws) -> dict[str, str]:
    """
    Parsea la pestaña especial TableDefinition.

    Retorna un dict: clave = nombre normalizado de la tabla propuesta,
                     valor = descripción funcional de la tabla.
    """
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return {}

    # Detectar si la primera fila es encabezado reconocible
    first_row = rows[0]
    has_header = False
    col_name_idx = 0   # columna del nombre de tabla
    col_desc_idx = 1   # columna de la descripción

    if first_row and any(c is not None for c in first_row):
        # Intentar reconocer encabezados
        name_aliases = {"tabla", "table", "nombre_tabla", "nombre", "table_name", "propuesta"}
        desc_aliases = {"definicion", "descripcion", "description", "definition",
                        "functional_definition", "funcional", "desc"}
        for idx, cell in enumerate(first_row):
            if cell is None:
                continue
            norm = re.sub(r"[\s_\-]+", "_", str(cell).strip().lower())
            if norm in name_aliases:
                col_name_idx = idx
                has_header = True
            if norm in desc_aliases:
                col_desc_idx = idx
                has_header = True

    data_rows = rows[1:] if has_header else rows

    result: dict[str, str] = {}
    for row in data_rows:
        if not any(c is not None and str(c).strip() for c in row):
            continue
        name_raw = row[col_name_idx] if col_name_idx < len(row) else None
        desc_raw = row[col_desc_idx] if col_desc_idx < len(row) else None

        name = str(name_raw).strip() if name_raw is not None else ""
        desc = str(desc_raw).strip() if desc_raw is not None else ""

        if name and desc:
            # Guardar con clave normalizada para matching flexible
            result[_normalize_table_name(name)] = desc

    return result


def _normalize_table_name(name: str) -> str:
    """Normaliza un nombre de tabla para matching flexible entre sheets."""
    return re.sub(r"[\s_\-]+", "_", name.strip().lower())


# ─── Parser principal ─────────────────────────────────────────────────────

@tool(approval_mode="never_require")
def parse_excel_file(
    file_path: Annotated[
        str,
        Field(description="Ruta absoluta o relativa al archivo .xlsx a procesar"),
    ],
) -> str:
    """Parsea un archivo Excel del usuario donde cada pestaña representa una
    tabla del modelo de datos a generar.

    Retorna un JSON con:
    - tables: lista de tablas con sus columnas y metadatos
    - table_definitions: dict con la descripción funcional de cada tabla
      (si existe la pestaña TableDefinition)
    - total_tables: número de tablas encontradas

    Cada tabla incluye:
    - table_name: nombre PROVISIONAL tomado del nombre de la pestaña
    - table_description: descripción funcional (de TableDefinition si existe)
    - is_proposed_name: true — indica que el nombre debe ser re-definido
      por el agente según los lineamientos de nomenclatura
    - columns: lista de columnas con nombre_provisional, tipo_provisional
      y definición funcional

    IMPORTANTE PARA EL AGENTE:
    - table_name es una SUGERENCIA, no el nombre físico final
    - column_name en cada columna es una SUGERENCIA provisional
    - data_type_hint es una INDICACIÓN; el tipo final sigue los lineamientos
    - El agente DEBE derivar todos los nombres finales aplicando las
      convenciones de nomenclatura de los guidelines (prefijos, abreviaturas,
      Parent Domain, etc.)
    """
    path = Path(file_path)
    if not path.exists():
        return json.dumps(
            {"error": f"Archivo no encontrado: {file_path}"},
            ensure_ascii=False,
        )
    if path.suffix.lower() != ".xlsx":
        return json.dumps(
            {"error": f"Formato no soportado: {path.suffix}. Solo .xlsx"},
            ensure_ascii=False,
        )

    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)

    # ── Paso 1: detectar y parsear pestaña TableDefinition ────────────────
    table_def_map: dict[str, str] = {}
    for sheet_name in wb.sheetnames:
        if _is_table_def_sheet(sheet_name):
            ws = wb[sheet_name]
            table_def_map = _parse_table_def_sheet(ws)
            break  # solo una pestaña de definiciones

    # ── Paso 2: parsear pestañas de tablas ────────────────────────────────
    tables: list[dict] = []

    for sheet_name in wb.sheetnames:
        # Saltear la pestaña de definiciones
        if _is_table_def_sheet(sheet_name):
            continue

        ws = wb[sheet_name]
        rows = list(ws.iter_rows(values_only=True))

        if not rows:
            continue

        # Intentar detectar encabezados en la primera fila
        raw_headers = rows[0]
        header_map: dict[int, str] = {}
        recognized_count = 0

        for idx, h in enumerate(raw_headers):
            if h is not None:
                matched = _match_header(str(h))
                if matched:
                    header_map[idx] = matched
                    recognized_count += 1

        # Si no se reconoció ningún encabezado, usar detección posicional
        # y tratar TODAS las filas como datos (la primera fila son datos, no headers)
        if recognized_count == 0:
            num_data_cols = sum(1 for c in raw_headers if c is not None)
            header_map = _build_positional_header_map(num_data_cols or len(raw_headers))
            data_rows = rows  # ningún header reconocido: la fila 0 son datos
        else:
            # Al menos un header fue reconocido → la fila 0 ES el encabezado
            data_rows = rows[1:]

        # Si se reconocieron algunos headers pero falta functional_definition,
        # completar por posición PERO mantener data_rows = rows[1:] ya que
        # la primera fila confirmó ser encabezado (recognized_count > 0)
        if "functional_definition" not in header_map.values():
            num_cols = len(raw_headers)
            fallback = _build_positional_header_map(num_cols)
            for pos, field in fallback.items():
                if pos not in header_map:
                    header_map[pos] = field
            # NO sobreescribir data_rows aquí: si recognized_count > 0 la
            # fila 0 es encabezado y ya se excluyó correctamente

        # ── Parsear filas de columnas ──────────────────────────────────
        columns: list[dict] = []
        for row in data_rows:
            if not any(cell is not None and str(cell).strip() for cell in row):
                continue

            col_data: dict = {}
            for idx, field_name in header_map.items():
                if idx < len(row):
                    value = row[idx]
                    if value is not None and str(value).strip():
                        if field_name == "is_nullable":
                            val_str = str(value).strip().lower()
                            col_data[field_name] = val_str in (
                                "true", "1", "si", "sí", "yes", "y", "s"
                            )
                        else:
                            # Strip spaces y caracteres de control de todos los valores
                            col_data[field_name] = str(value).strip().rstrip()

            # Solo incluir si tiene al menos la definición funcional
            if col_data.get("functional_definition"):
                columns.append(col_data)

        if not columns:
            continue

        # Buscar descripción de tabla en TableDefinition
        norm_sheet = _normalize_table_name(sheet_name)
        table_description = table_def_map.get(norm_sheet, "")

        tables.append({
            "table_name": sheet_name.strip(),
            "table_description": table_description,
            # Señal explícita para el agente: este nombre es provisional
            "is_proposed_name": True,
            "columns": columns,
        })

    wb.close()

    if not tables:
        return json.dumps(
            {"error": "No se encontraron tablas válidas en el archivo Excel."},
            ensure_ascii=False,
        )

    return json.dumps(
        {
            "tables": tables,
            "total_tables": len(tables),
            "has_table_definitions": bool(table_def_map),
            "note": (
                "Los nombres de tablas y columnas son PROVISIONALES. "
                "El agente debe derivar los nombres físicos finales aplicando "
                "los lineamientos de nomenclatura corporativa."
            ),
        },
        indent=2,
        ensure_ascii=False,
    )
