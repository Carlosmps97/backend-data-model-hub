"""Construcción del `ModelingResponseAPI` final.

Toma:
- La lista de tablas crudas que produjo el LLM (una por tabla del Excel).
- El motor destino.
- El estado de conversación (para meta).
- Los guidelines activos (para inyectar audit columns).

Y produce el `ModelingResponseAPI` que el frontend consume.

El pipeline es 100 % determinista a partir del JSON del LLM:
1. Mapea el JSON a `TableAPI` (sin DDL, sin audit columns todavía).
2. Inyecta audit columns desde los guidelines.
3. Genera DDL por motor con templates de Python.
4. Construye el export Markdown.

Sin QA report, sin standardized columns, sin relationships, sin
matching tolerante. Si el LLM se confundió en un nombre, el revisor
humano lo edita en el frontend — no es responsabilidad del backend
"recuperarlo".
"""

from __future__ import annotations

import json
from typing import Any

from src.conversation import ConversationState
from src.processing.audit_columns import inject_audit_columns
from src.processing.ddl_generator import (
    render_export_sql,
    render_table_ddl,
)
from src.processing.markdown_generator import render_export_markdown
from src.schemas import (
    ColumnAPI,
    ModelingResponseAPI,
    QAReportAPI,
    TableAPI,
)
from src.tools.knowledge_base_tools import _resolve_guidelines


# ─── Parseo defensivo ───────────────────────────────────────────────────


def _parse_json(value: Any) -> dict:
    """Parsea un JSON string a dict; tolera dicts ya parseados y errores."""
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _coerce_bool(value: Any, default: bool = False) -> bool:
    """Convierte valores tolerantemente a bool."""
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("true", "1", "yes", "y", "si", "sí"):
            return True
        if v in ("false", "0", "no", "n"):
            return False
    return default


# ─── Mapeo del JSON del LLM al schema API ──────────────────────────────


def _map_column(raw: dict) -> ColumnAPI:
    """Convierte un dict de columna del LLM a `ColumnAPI`.

    Contrato esperado del LLM:
        {column_name, data_type, is_pk, nullable, functional_definition}

    Tolera nombres legacy: `is_primary_key`, `is_nullable`.
    Si el LLM omitió algún campo, usa defaults razonables.
    """
    name = str(raw.get("column_name") or "").strip()
    data_type = str(raw.get("data_type") or "STRING").strip()
    is_pk = _coerce_bool(raw.get("is_pk", raw.get("is_primary_key")))
    nullable = _coerce_bool(raw.get("nullable", raw.get("is_nullable")), default=True)
    if is_pk:
        nullable = False  # PK nunca puede ser nullable
    fdef = str(raw.get("functional_definition") or "").strip()

    return ColumnAPI(
        column_name=name,
        data_type=data_type,
        nullable=nullable,
        is_pk=is_pk,
        is_fk=False,
        fk_references=None,
        functional_definition=fdef,
        observations="",
    )


def _map_table(raw: dict) -> TableAPI:
    """Convierte un dict de tabla del LLM a `TableAPI` (sin DDL aún)."""
    return TableAPI(
        table_name=str(raw.get("table_name") or "").strip(),
        table_description=str(raw.get("table_description") or "").strip(),
        columns=[_map_column(c) for c in (raw.get("columns") or [])],
        ddl="",
    )


# ─── Ensamblado final del modelo ───────────────────────────────────────


def assemble_tables(
    raw_tables: list[dict],
    engine: str,
    guidelines: dict | None = None,
) -> list[TableAPI]:
    """Convierte el output crudo del LLM en la lista final de `TableAPI`.

    Pasos por tabla:
        1. Map columnas del LLM → `ColumnAPI`.
        2. Inyectar audit columns desde guidelines.
        3. Generar DDL con template del motor.

    Si `guidelines` es None, se intentan resolver del context activo
    (sesión o config global).
    """
    g = guidelines if guidelines is not None else _resolve_guidelines()
    out: list[TableAPI] = []
    for raw in raw_tables:
        table = _map_table(raw)
        if not table.table_name:
            continue
        table.columns = inject_audit_columns(table.columns, table.table_name, g)
        table.ddl = render_table_ddl(table, engine)
        out.append(table)
    return out


def build_model_payload(
    raw_tables: list[dict],
    engine: str,
    guidelines: dict | None = None,
) -> dict[str, Any]:
    """Devuelve un dict serializable con el modelo ensamblado.

    Útil para persistir en `state.last_model` sin armar todavía el
    response API completo.
    """
    tables = assemble_tables(raw_tables, engine, guidelines)
    return {
        "engine": engine,
        "tables": [t.model_dump() for t in tables],
    }


def build_modeling_response(
    state: ConversationState,
    raw_tables: list[dict],
    engine: str,
    guidelines: dict | None = None,
) -> ModelingResponseAPI:
    """Construye el `ModelingResponseAPI` listo para devolver al frontend."""
    tables = assemble_tables(raw_tables, engine, guidelines)
    export_sql = render_export_sql(tables, engine)
    export_md = render_export_markdown(tables, engine)

    guidelines_applied = _summarize_guidelines_applied(state)

    return ModelingResponseAPI(
        conversation_id=state.conversation_id,
        engine=engine,
        turn_number=max(state.turn_number, 1),
        tables=tables,
        relationships=[],
        export_sql=export_sql,
        export_markdown=export_md,
        qa_report=QAReportAPI(),
        guidelines_applied=guidelines_applied,
        summary="",
    )


def build_modeling_response_from_payload(
    state: ConversationState,
    payload: dict[str, Any],
) -> ModelingResponseAPI:
    """Reconstruye el `ModelingResponseAPI` a partir de un payload guardado.

    Se usa en GET /api/conversations/{id}/model para devolver el último
    modelo sin reejecutar el agente. Las tablas en `payload` ya pasaron
    por `assemble_tables`, así que solo regeneramos los exportes.
    """
    raw_tables = payload.get("tables") or []
    engine = str(payload.get("engine") or state.engine)
    tables = [TableAPI(**t) for t in raw_tables]
    export_sql = render_export_sql(tables, engine)
    export_md = render_export_markdown(tables, engine)

    return ModelingResponseAPI(
        conversation_id=state.conversation_id,
        engine=engine,
        turn_number=max(state.turn_number, 1),
        tables=tables,
        relationships=[],
        export_sql=export_sql,
        export_markdown=export_md,
        qa_report=QAReportAPI(),
        guidelines_applied=_summarize_guidelines_applied(state),
        summary="",
    )


def _summarize_guidelines_applied(state: ConversationState) -> str:
    """Texto corto descriptivo de qué lineamientos se aplicaron."""
    if state.guidelines_meta is not None:
        meta = state.guidelines_meta
        return (
            f"Lineamientos de sesión: {meta.file_name} "
            f"({meta.file_format}, {meta.file_size_bytes} bytes)"
        )
    return "Lineamientos por defecto del sistema."
