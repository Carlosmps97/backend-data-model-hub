"""Herramientas para gestión del catálogo corporativo de columnas.

Proporciona búsqueda semántica/keyword y operaciones de escritura
thread-safe sobre el archivo column_catalog.json.
"""

import json
import threading
from pathlib import Path
from typing import Annotated

from agent_framework import tool
from pydantic import Field

from src.config import settings

# Lock global para operaciones thread-safe sobre el catálogo
_catalog_lock = threading.Lock()


def _load_catalog() -> dict:
    """Carga el catálogo de columnas desde el archivo JSON."""
    path = Path(settings.COLUMN_CATALOG_PATH)
    if not path.exists():
        return {"columns": []}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_catalog(catalog: dict) -> None:
    """Guarda el catálogo de columnas al archivo JSON."""
    path = Path(settings.COLUMN_CATALOG_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)


def _keyword_similarity(text1: str, text2: str) -> float:
    """Calcula similitud por keywords entre dos textos."""
    words1 = set(text1.lower().split())
    words2 = set(text2.lower().split())
    # Eliminar stopwords comunes en español e inglés
    stopwords = {
        "de", "del", "la", "el", "los", "las", "un", "una", "y", "o", "en",
        "a", "que", "por", "para", "con", "es", "se", "su", "al",
        "the", "a", "an", "of", "and", "or", "in", "to", "for", "is",
    }
    words1 -= stopwords
    words2 -= stopwords
    if not words1 or not words2:
        return 0.0
    intersection = words1 & words2
    union = words1 | words2
    return len(intersection) / len(union)


@tool(approval_mode="never_require")
def search_column_catalog(
    functional_definition: Annotated[
        str,
        Field(
            description=(
                "Definición funcional de la columna a buscar en el catálogo. "
                "Ejemplo: 'Fecha de ejecución de un proceso o rutina'"
            )
        ),
    ],
) -> str:
    """Busca en el catálogo corporativo columnas con definición funcional similar.

    Retorna las columnas más similares encontradas con su score de similitud.
    Si encuentra una columna con similitud >= 0.5, se debe usar el nombre estandarizado.
    """
    with _catalog_lock:
        catalog = _load_catalog()

    matches = []
    for col in catalog.get("columns", []):
        score = _keyword_similarity(functional_definition, col["functional_definition"])
        if score >= 0.3:
            matches.append({
                "column_name": col["column_name"],
                "functional_definition": col["functional_definition"],
                "data_type": col["data_type"],
                "used_in_tables": col["used_in_tables"],
                "similarity_score": round(score, 3),
            })

    matches.sort(key=lambda x: x["similarity_score"], reverse=True)

    if matches:
        return json.dumps(
            {
                "found": True,
                "matches": matches[:5],
                "best_match": matches[0],
                "recommendation": (
                    f"USAR nombre estandarizado: '{matches[0]['column_name']}' "
                    f"(similitud: {matches[0]['similarity_score']})"
                    if matches[0]["similarity_score"] >= 0.5
                    else "No hay coincidencia fuerte. Se puede usar el nombre propuesto."
                ),
            },
            indent=2,
            ensure_ascii=False,
        )

    return json.dumps(
        {
            "found": False,
            "matches": [],
            "recommendation": "No se encontró columna similar en el catálogo. Usar nombre propuesto y agregar al catálogo.",
        },
        indent=2,
        ensure_ascii=False,
    )


@tool(approval_mode="never_require")
def add_column_to_catalog(
    column_name: Annotated[str, Field(description="Nombre de la columna a agregar")],
    functional_definition: Annotated[str, Field(description="Definición funcional de la columna")],
    data_type: Annotated[str, Field(description="Tipo de dato base de la columna")],
    table_name: Annotated[str, Field(description="Nombre de la tabla donde se usa la columna")],
) -> str:
    """Agrega una nueva columna al catálogo corporativo si no existe ya.

    La columna se marca como 'nueva' (is_new=true) para revisión posterior.
    """
    with _catalog_lock:
        catalog = _load_catalog()

        # Verificar si ya existe
        for col in catalog.get("columns", []):
            if col["column_name"] == column_name:
                if table_name not in col["used_in_tables"]:
                    col["used_in_tables"].append(table_name)
                    _save_catalog(catalog)
                    return json.dumps(
                        {"status": "updated", "message": f"Tabla '{table_name}' agregada a columna existente '{column_name}'."},
                        ensure_ascii=False,
                    )
                return json.dumps(
                    {"status": "exists", "message": f"La columna '{column_name}' ya existe en el catálogo."},
                    ensure_ascii=False,
                )

        # Agregar nueva columna
        new_entry = {
            "column_name": column_name,
            "functional_definition": functional_definition,
            "data_type": data_type,
            "used_in_tables": [table_name],
            "is_new": True,
        }
        catalog.setdefault("columns", []).append(new_entry)
        _save_catalog(catalog)

    return json.dumps(
        {"status": "created", "message": f"Columna '{column_name}' agregada al catálogo como nueva.", "entry": new_entry},
        indent=2,
        ensure_ascii=False,
    )


@tool(approval_mode="never_require")
def get_full_column_catalog() -> str:
    """Retorna el catálogo completo de columnas corporativas para validación integral."""
    with _catalog_lock:
        catalog = _load_catalog()
    return json.dumps(catalog, indent=2, ensure_ascii=False)
