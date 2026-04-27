"""Construcción de la respuesta API a partir del output del workflow.

El workflow devuelve un dict con `engine`, `generated_model` (string JSON
limpio del ExecutorAgent) y `qa_validation` (string JSON limpio del
QAValidatorAgent). Esta capa:

- Parsea ambos JSON.
- Mapea los nombres internos del agente (is_primary_key, is_nullable,
  fk_reference, ...) a los nombres del contrato API (is_pk, nullable,
  fk_references, ...).
- Prefiere las tablas del QA (que pueden tener nombres estandarizados) sobre
  las del Executor cuando ambas existen.
- Genera `export_sql` (concatenación de DDLs) y `export_markdown` (modelo
  formateado en Markdown).
- No inventa contenido: si un campo falta en la salida del agente, devuelve
  default (string vacío, lista vacía, etc.).
"""

from __future__ import annotations

import json
from typing import Any

from src.conversation import ConversationState
from src.schemas import (
    ColumnAPI,
    GuidelineViolationAPI,
    ModelingResponseAPI,
    NewCatalogEntryAPI,
    QAReportAPI,
    StandardizedColumnAPI,
    TableAPI,
)


# ─── Parseo defensivo ───────────────────────────────────────────────────


def _normalize_relationship(item: Any) -> str:
    """Convierte un item de relación a string, tolerando dicts del agente.

    El LLM a veces devuelve relaciones como dicts con from_table/to_table
    en lugar de strings. Esta función los aplana a texto legible sin romper
    el contrato `list[str]` del schema API.
    """
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        from_t = (
            item.get("from_table") or item.get("source_table")
            or item.get("table_from") or item.get("from") or ""
        )
        to_t = (
            item.get("to_table") or item.get("target_table")
            or item.get("table_to") or item.get("to") or ""
        )
        col_from = item.get("from_column") or item.get("source_column") or ""
        col_to = item.get("to_column") or item.get("target_column") or ""
        rel_type = item.get("type") or item.get("relationship_type") or ""
        description = item.get("description") or item.get("desc") or ""

        if from_t and to_t:
            src = f"{from_t}.{col_from}" if col_from else from_t
            tgt = f"{to_t}.{col_to}" if col_to else to_t
            link = f"{src} → {tgt}"
            if rel_type:
                link = f"{link} [{rel_type}]"
            return f"{link} — {description}".rstrip(" —") if description else link
        if description:
            return description
        return str(item)
    return str(item)


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


# ─── Mapeo de columna interna → ColumnAPI ───────────────────────────────


def _map_column(raw: dict) -> ColumnAPI:
    """Mapea un dict de columna del agente al schema API."""
    fk_ref_raw = raw.get("fk_reference") or raw.get("fk_references")
    fk_references = (
        fk_ref_raw if isinstance(fk_ref_raw, str) and fk_ref_raw else None
    )

    constraints = raw.get("constraints") or []
    default_value = raw.get("default_value")
    notes = raw.get("notes") or raw.get("observations") or ""

    # `observations` consolida notas + constraints + default si los hay.
    obs_parts: list[str] = []
    if notes:
        obs_parts.append(str(notes))
    if default_value not in (None, ""):
        obs_parts.append(f"default={default_value}")
    if constraints:
        if isinstance(constraints, list):
            obs_parts.append("constraints=" + ", ".join(str(c) for c in constraints))
        else:
            obs_parts.append(f"constraints={constraints}")
    observations = " | ".join(obs_parts)

    return ColumnAPI(
        column_name=str(raw.get("column_name") or ""),
        data_type=str(raw.get("data_type") or ""),
        nullable=_coerce_bool(raw.get("is_nullable", raw.get("nullable")), default=True),
        is_pk=_coerce_bool(raw.get("is_primary_key", raw.get("is_pk")), default=False),
        is_fk=_coerce_bool(raw.get("is_foreign_key", raw.get("is_fk")), default=False),
        fk_references=fk_references,
        functional_definition=str(raw.get("functional_definition") or ""),
        observations=observations,
    )


def _map_table(raw: dict) -> TableAPI:
    """Mapea un dict de tabla del agente al schema API."""
    return TableAPI(
        table_name=str(raw.get("table_name") or ""),
        table_description=str(raw.get("notes") or raw.get("table_description") or ""),
        columns=[_map_column(c) for c in (raw.get("columns") or [])],
        ddl=str(raw.get("ddl") or ""),
    )


# ─── QA report ──────────────────────────────────────────────────────────


def _map_qa_report(qa_data: dict) -> QAReportAPI:
    """Mapea el reporte de QA al schema API."""
    qa_report = qa_data.get("qa_report") or {}

    return QAReportAPI(
        quality_score=int(qa_report.get("quality_score") or 0),
        standardized_columns=[
            StandardizedColumnAPI(
                table_name=str(item.get("table_name") or ""),
                original_name=str(item.get("original_name") or ""),
                standardized_name=str(item.get("standardized_name") or ""),
                reason=str(item.get("reason") or ""),
            )
            for item in (qa_report.get("standardized_columns") or [])
        ],
        guideline_violations=[
            GuidelineViolationAPI(
                table_name=str(item.get("table_name") or ""),
                column_name=str(item.get("column_name") or ""),
                violation=str(item.get("violation") or ""),
                correction_applied=str(item.get("correction_applied") or ""),
            )
            for item in (qa_report.get("guideline_violations") or [])
        ],
        new_catalog_entries=[
            NewCatalogEntryAPI(
                column_name=str(item.get("column_name") or ""),
                functional_definition=str(item.get("functional_definition") or ""),
                data_type=str(item.get("data_type") or ""),
                table_name=str(item.get("table_name") or ""),
            )
            for item in (qa_report.get("new_catalog_entries") or [])
        ],
        summary=str(qa_report.get("summary") or ""),
    )


# ─── Exportes ───────────────────────────────────────────────────────────


def _build_export_sql(tables: list[TableAPI]) -> str:
    """Concatena los DDLs de todas las tablas, separados por header."""
    chunks: list[str] = []
    for t in tables:
        if not t.ddl.strip():
            continue
        header = f"-- ── Tabla: {t.table_name} ──"
        chunks.append(f"{header}\n{t.ddl.strip()}")
    return "\n\n".join(chunks)


def _markdown_escape(text: str) -> str:
    """Escapa pipes para que no rompan tablas Markdown."""
    return text.replace("|", "\\|").replace("\n", " ")


def _build_export_markdown(
    tables: list[TableAPI],
    relationships: list[str],
    summary: str,
) -> str:
    """Genera un Markdown completo del modelo (tablas + DDL + relaciones)."""
    lines: list[str] = ["# Modelo de Datos", ""]

    if summary:
        lines.append(summary)
        lines.append("")

    for t in tables:
        lines.append(f"## {t.table_name}")
        if t.table_description:
            lines.append("")
            lines.append(t.table_description)
        lines.append("")
        lines.append("| Columna | Tipo | Nullable | PK | FK | Definición funcional | Observaciones |")
        lines.append("|---|---|:-:|:-:|:-:|---|---|")
        for c in t.columns:
            fk_cell = c.fk_references or ""
            row = (
                f"| {_markdown_escape(c.column_name)} "
                f"| {_markdown_escape(c.data_type)} "
                f"| {'✓' if c.nullable else '✗'} "
                f"| {'✓' if c.is_pk else ''} "
                f"| {'✓' if c.is_fk else ''} "
                f"| {_markdown_escape(c.functional_definition)} "
                f"| {_markdown_escape(c.observations)} |"
            )
            lines.append(row)
        lines.append("")
        if t.ddl.strip():
            lines.append("```sql")
            lines.append(t.ddl.strip())
            lines.append("```")
            lines.append("")

    if relationships:
        lines.append("## Relaciones")
        for r in relationships:
            lines.append(f"- {r}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _summarize_guidelines_applied(
    state: ConversationState,
    model_summary: str,
) -> str:
    """Genera el campo `guidelines_applied` para el response.

    Sin inventar: si la sesión cargó un archivo, lo nombra; si no, indica
    que se usaron los lineamientos por defecto del sistema. Anexa el
    resumen del agente cuando el agente lo provee.
    """
    parts: list[str] = []
    if state.guidelines_meta is not None:
        meta = state.guidelines_meta
        parts.append(
            f"Lineamientos de sesión: {meta.file_name} "
            f"({meta.file_format}, {meta.file_size_bytes} bytes)"
        )
    else:
        parts.append("Lineamientos por defecto del sistema.")
    if model_summary:
        parts.append(model_summary.strip())
    return " — ".join(parts)


# ─── API pública ────────────────────────────────────────────────────────


def build_model_payload(workflow_result: dict) -> dict[str, Any]:
    """Devuelve un dict con tables/relationships/qa_report parseados.

    Útil cuando solo se quiere persistir el último modelo en el store sin
    ensamblar todavía el `ModelingResponseAPI` final. La forma del payload
    es estable y serializable como JSON.
    """
    generated = _parse_json(workflow_result.get("generated_model"))
    qa = _parse_json(workflow_result.get("qa_validation"))

    # Preferimos las tablas del QA si las trae (pueden tener nombres
    # estandarizados y DDL regenerado).
    raw_tables = qa.get("tables") or generated.get("tables") or []
    tables = [_map_table(t) for t in raw_tables]
    relationships = [_normalize_relationship(r) for r in (generated.get("relationships") or [])]
    qa_report = _map_qa_report(qa)
    summary = str(generated.get("summary") or "")

    return {
        "tables": [t.model_dump() for t in tables],
        "relationships": relationships,
        "qa_report": qa_report.model_dump(),
        "summary": summary,
    }


def build_modeling_response(
    state: ConversationState,
    workflow_result: dict,
) -> ModelingResponseAPI:
    """Construye el `ModelingResponseAPI` final a partir del output bruto.

    `state` se usa solo para meta (conversation_id, engine, turn_number,
    guidelines de sesión).
    """
    generated = _parse_json(workflow_result.get("generated_model"))
    qa = _parse_json(workflow_result.get("qa_validation"))

    raw_tables = qa.get("tables") or generated.get("tables") or []
    tables = [_map_table(t) for t in raw_tables]
    relationships = [_normalize_relationship(r) for r in (generated.get("relationships") or [])]
    qa_report = _map_qa_report(qa)
    model_summary = str(generated.get("summary") or "")

    export_sql = _build_export_sql(tables)
    export_markdown = _build_export_markdown(tables, relationships, model_summary)
    guidelines_applied = _summarize_guidelines_applied(state, model_summary)

    return ModelingResponseAPI(
        conversation_id=state.conversation_id,
        engine=str(workflow_result.get("engine") or state.engine),
        turn_number=state.turn_number,
        tables=tables,
        relationships=relationships,
        export_sql=export_sql,
        export_markdown=export_markdown,
        qa_report=qa_report,
        guidelines_applied=guidelines_applied,
        summary=model_summary,
    )


def build_modeling_response_from_payload(
    state: ConversationState,
    payload: dict[str, Any],
) -> ModelingResponseAPI:
    """Reconstruye el `ModelingResponseAPI` a partir de un payload guardado.

    Usado por GET /api/conversations/{id}/model para devolver el último
    modelo sin reejecutar el agente.
    """
    tables = [TableAPI(**t) for t in (payload.get("tables") or [])]
    relationships = [_normalize_relationship(r) for r in (payload.get("relationships") or [])]
    qa_report = QAReportAPI(**(payload.get("qa_report") or {}))
    model_summary = str(payload.get("summary") or "")

    export_sql = _build_export_sql(tables)
    export_markdown = _build_export_markdown(tables, relationships, model_summary)
    guidelines_applied = _summarize_guidelines_applied(state, model_summary)

    return ModelingResponseAPI(
        conversation_id=state.conversation_id,
        engine=state.engine,
        turn_number=max(state.turn_number, 1),
        tables=tables,
        relationships=relationships,
        export_sql=export_sql,
        export_markdown=export_markdown,
        qa_report=qa_report,
        guidelines_applied=guidelines_applied,
        summary=model_summary,
    )
