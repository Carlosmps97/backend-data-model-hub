"""Herramienta de conversión de documentos (PDF/DOCX) a Markdown.

Usa Docling (DocumentConverter) para convertir archivos a Markdown,
preservando estructura, tablas y jerarquía de encabezados.
El resultado se cachea en disco para evitar reconversiones innecesarias.
"""

import os
from pathlib import Path
from typing import Annotated

from agent_framework import tool
from pydantic import Field


def _is_cache_fresh(source_path: Path, md_path: Path) -> bool:
    """Verifica si el archivo .md cacheado es más reciente que el original."""
    if not md_path.exists():
        return False
    return os.path.getmtime(md_path) >= os.path.getmtime(source_path)


def _convert_document(source_path: Path) -> Path:
    """Convierte un archivo PDF o DOCX a Markdown usando Docling.

    Si ya existe un .md cacheado más reciente que el original, lo retorna
    sin reconvertir. El .md se guarda en la misma carpeta con el mismo
    nombre base.

    Args:
        source_path: Ruta al archivo .pdf o .docx a convertir.

    Returns:
        Ruta al archivo .md generado o cacheado.

    Raises:
        FileNotFoundError: Si el archivo origen no existe.
        ValueError: Si la extensión no es .pdf o .docx.
    """
    source_path = Path(source_path).resolve()

    if not source_path.exists():
        raise FileNotFoundError(f"Archivo no encontrado: {source_path}")

    suffix = source_path.suffix.lower()
    if suffix not in (".pdf", ".docx"):
        raise ValueError(f"Formato no soportado para conversión: {suffix}. Solo .pdf y .docx.")

    # Ruta del .md cacheado (mismo directorio, mismo nombre base)
    md_path = source_path.with_suffix(".md")

    # Si el cache es fresco, retornar sin reconvertir
    if _is_cache_fresh(source_path, md_path):
        return md_path

    # Convertir con Docling
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    result = converter.convert(source_path)
    markdown_text = result.document.export_to_markdown()

    # Guardar .md en disco
    md_path.write_text(markdown_text, encoding="utf-8")

    return md_path


@tool(approval_mode="never_require")
def convert_to_markdown(
    file_path: Annotated[
        str,
        Field(
            description=(
                "Ruta absoluta o relativa al archivo .pdf o .docx que se desea "
                "convertir a Markdown. El archivo .md resultante se guarda en la "
                "misma carpeta con el mismo nombre base."
            )
        ),
    ],
) -> str:
    """Convierte un archivo PDF o DOCX a Markdown usando Docling.

    Útil para pre-procesar knowledge bases en formatos binarios antes de
    que los agentes los interpreten. El Markdown preserva la estructura
    del documento (encabezados, tablas, listas).

    Si ya existe un .md cacheado más reciente que el original, lo retorna
    sin reconvertir.
    """
    try:
        source_path = Path(file_path).resolve()
        md_path = _convert_document(source_path)
        md_content = md_path.read_text(encoding="utf-8")

        return (
            f'{{"status": "success", '
            f'"source": "{source_path}", '
            f'"output": "{md_path}", '
            f'"content_length": {len(md_content)}, '
            f'"message": "Archivo convertido exitosamente a Markdown."}}'
        )
    except FileNotFoundError as e:
        return f'{{"status": "error", "message": "{e}"}}'
    except ValueError as e:
        return f'{{"status": "error", "message": "{e}"}}'
    except Exception as e:
        return f'{{"status": "error", "message": "Error durante la conversión: {e}"}}'
