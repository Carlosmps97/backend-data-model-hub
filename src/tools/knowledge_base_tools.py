"""Herramientas del KnowledgeBaseAgent — carga y consulta de lineamientos de modelamiento.

Soporta múltiples formatos de fuente: JSON, XLSX, DOCX, PDF, TXT.
La ruta se configura en .env (GUIDELINES_PATH).
"""

import json
from pathlib import Path
from typing import Annotated

from agent_framework import tool
from pydantic import Field

from src.config import settings

# Cache global de lineamientos
_guidelines_cache: dict | None = None


def _load_guidelines() -> dict:
    """Carga los lineamientos desde la fuente configurada."""
    global _guidelines_cache
    if _guidelines_cache is not None:
        return _guidelines_cache

    path = Path(settings.GUIDELINES_PATH)
    if not path.exists():
        return {"error": f"No se encontró el archivo de lineamientos: {path}"}

    suffix = path.suffix.lower()

    if suffix == ".json":
        with open(path, "r", encoding="utf-8") as f:
            _guidelines_cache = json.load(f)

    elif suffix == ".xlsx":
        import openpyxl

        wb = openpyxl.load_workbook(path, read_only=True)
        data = {}
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            rows = []
            headers = None
            for i, row in enumerate(ws.iter_rows(values_only=True)):
                if i == 0:
                    headers = [str(c).strip().lower() if c else f"col_{j}" for j, c in enumerate(row)]
                else:
                    rows.append(dict(zip(headers, row)))
            data[sheet_name] = rows
        wb.close()
        _guidelines_cache = {"format": "xlsx", "sheets": data}

    elif suffix in (".docx", ".pdf"):
        # Convertir a Markdown con Docling para preservar estructura
        from src.tools.convert_tools import _convert_document

        try:
            md_path = _convert_document(path)
            text = md_path.read_text(encoding="utf-8")
            _guidelines_cache = {"format": "markdown", "content": text}
        except Exception as e:
            _guidelines_cache = {"error": f"Error al convertir {suffix} a Markdown: {e}"}

    elif suffix in (".md", ".txt", ".text"):
        text = path.read_text(encoding="utf-8")
        fmt = "markdown" if suffix == ".md" else "text"
        _guidelines_cache = {"format": fmt, "content": text}

    else:
        _guidelines_cache = {"error": f"Formato no soportado: {suffix}"}

    return _guidelines_cache


@tool(approval_mode="never_require")
def query_guidelines(
    topic: Annotated[
        str,
        Field(
            description=(
                "Tema a consultar en los lineamientos de modelamiento. "
                "Ejemplos: 'naming_conventions', 'audit_columns', 'data_type_mappings', "
                "'primary_key_conventions', 'foreign_key_conventions', 'general_rules'. "
                "También puede ser un motor de BD como 'databricks_sql', 'sqlserver', etc."
            )
        ),
    ],
) -> str:
    """Consulta los lineamientos corporativos de modelamiento de datos por tema específico."""
    guidelines = _load_guidelines()

    if "error" in guidelines:
        return json.dumps(guidelines)

    # Si es JSON estructurado, buscar por clave
    if isinstance(guidelines, dict) and "format" not in guidelines:
        topic_lower = topic.lower().strip()

        # Buscar directamente en las claves del JSON
        if topic_lower in guidelines:
            return json.dumps(guidelines[topic_lower], indent=2, ensure_ascii=False)

        # Buscar en data_type_mappings por motor
        if topic_lower in guidelines.get("data_type_mappings", {}):
            return json.dumps(
                {"engine": topic_lower, "type_mappings": guidelines["data_type_mappings"][topic_lower]},
                indent=2,
                ensure_ascii=False,
            )

        # Búsqueda por keywords en todas las claves
        results = {}
        for key, value in guidelines.items():
            if topic_lower in key.lower() or (
                isinstance(value, (dict, list))
                and topic_lower in json.dumps(value, ensure_ascii=False).lower()
            ):
                results[key] = value

        if results:
            return json.dumps(results, indent=2, ensure_ascii=False)

        return json.dumps(
            {"message": f"No se encontraron lineamientos para el tema: {topic}"},
            ensure_ascii=False,
        )

    # Si es texto plano (de docx, pdf, txt)
    if "content" in guidelines:
        content = guidelines["content"]
        lines = content.split("\n")
        relevant = [l for l in lines if topic.lower() in l.lower()]
        if relevant:
            return "\n".join(relevant)
        return f"No se encontró información específica sobre '{topic}' en los lineamientos."

    return json.dumps(guidelines, indent=2, ensure_ascii=False)


@tool(approval_mode="never_require")
def get_all_guidelines() -> str:
    """Retorna todos los lineamientos corporativos de modelamiento de datos completos."""
    guidelines = _load_guidelines()
    return json.dumps(guidelines, indent=2, ensure_ascii=False)
