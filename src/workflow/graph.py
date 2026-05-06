"""Workflow ultra-simplificado: 1 tabla por ejecución.

Grafo:

    build_prompt → executor_agent → emit_json

El input es **una sola tabla** del Excel del usuario más los metadatos
(motor destino, conversation_id para resolver guidelines de sesión).
El output es el JSON crudo que el LLM produjo según el contrato del
prompt: `{table_name, table_description, columns, notes?}`.

Lo que NO hace este workflow (intencionalmente):
- No procesa varias tablas en una llamada → la concurrencia se maneja
  desde la route, llamando este workflow N veces en paralelo.
- No genera DDL → lo arma `processing/ddl_generator.py` después.
- No inyecta audit columns → las agrega `processing/audit_columns.py`.
- No corre QA → eliminado.
- No revalida nombres por código → confiamos en el LLM porque el prompt
  es estricto y los lineamientos están en formato JSON consultable.
"""

from __future__ import annotations

import json
import re

from agent_framework import (
    AgentExecutorRequest,
    AgentExecutorResponse,
    Message,
    WorkflowBuilder,
    WorkflowContext,
    executor,
)
from agent_framework.foundry import FoundryChatClient
from typing_extensions import Never

from src.agents.factory import create_executor_agent
from src.config import settings
from src.db.catalog_db import embed_texts, search_similar_columns


def _fix_trailing_commas(text: str) -> str:
    """Elimina trailing commas antes de } o ] que el LLM a veces genera."""
    return re.sub(r",\s*([}\]])", r"\1", text)


def _extract_json_from_text(text: str) -> str:
    """Devuelve un string JSON válido a partir del output crudo del LLM.

    Tolera:
    - JSON puro.
    - JSON envuelto en ```json ... ``` o ``` ... ```.
    - Preamble en lenguaje natural antes del primer `{`.
    - Trailing commas.
    """
    if not text:
        return "{}"
    t = text.strip()

    def _try(candidate: str) -> str | None:
        for attempt in (candidate, _fix_trailing_commas(candidate)):
            try:
                json.loads(attempt)
                return attempt
            except json.JSONDecodeError:
                continue
        return None

    fence = re.search(r"```(?:json)?\s*\n?(\{.*\})\s*```", t, re.DOTALL)
    if fence:
        candidate = fence.group(1).strip()
        return _try(candidate) or _fix_trailing_commas(candidate)

    if t.startswith("{"):
        result = _try(t)
        if result:
            return result
        # Fallback: cortar en el último `}` balanceado.
        depth = 0
        end = -1
        for i, c in enumerate(t):
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = i
        if end > 0:
            candidate = t[: end + 1]
            return _try(candidate) or _fix_trailing_commas(candidate)
        return _fix_trailing_commas(t)

    first = t.find("{")
    last = t.rfind("}")
    if first >= 0 and last > first:
        candidate = t[first : last + 1]
        return _try(candidate) or _fix_trailing_commas(candidate)
    return t


def _format_columns_block(columns: list[dict]) -> str:
    """Renderiza la lista de columnas del input como bullets compactos.

    Una columna por línea: `index. nombre_provisional (tipo_hint) — definición funcional`.
    """
    lines: list[str] = []
    for i, col in enumerate(columns, 1):
        name = col.get("column_name") or ""
        dtype = col.get("data_type_hint") or ""
        fdef = col.get("functional_definition") or ""
        bits: list[str] = [f"{i}."]
        if name:
            bits.append(f"`{name}`")
        if dtype:
            bits.append(f"({dtype})")
        if fdef:
            bits.append(f"— {fdef}")
        lines.append("   " + " ".join(bits))
    return "\n".join(lines)


async def _build_semantic_dict(columns: list[dict]) -> str:
    """Consulta el catálogo vectorial y genera el bloque de diccionario mandatorio.

    Genera embeddings en un solo batch para todas las columnas con definición
    funcional, luego busca coincidencias históricas por encima del umbral de
    similitud. Si no hay coincidencias o la API de embeddings falla, retorna ''
    para que el flujo continúe sin interrupciones.
    """
    non_empty = [
        (i, col)
        for i, col in enumerate(columns)
        if (col.get("functional_definition") or "").strip()
    ]
    if not non_empty:
        return ""

    try:
        texts = [col.get("functional_definition", "") for _, col in non_empty]
        embeddings = await embed_texts(texts)
        if not embeddings or len(embeddings) != len(texts):
            return ""

        matches: list[str] = []
        for (_idx, col), emb in zip(non_empty, embeddings):
            fdef = col.get("functional_definition", "")
            similar = await search_similar_columns(
                emb,
                limit=3,
                threshold=settings.VECTOR_SIMILARITY_THRESHOLD,
            )
            if not similar:
                continue
            best = similar[0]
            col_name = best.get("column_name") or str(best.get("_id", ""))
            data_type = best.get("data_type", "")
            score = best.get("score", 0.0)
            matches.append(
                f'- Definicion: "{fdef}" '
                f'-> Nombre EXACTO: `{col_name}` | Tipo: {data_type} | Similitud: {score:.2f}'
            )

        if not matches:
            return ""

        lines = [
            "## DICCIONARIO SEMANTICO MANDATORIO",
            "Las siguientes columnas ya existen en el catalogo historico con alta similitud semantica.",
            "DEBES usar EXACTAMENTE estos nombres fisicos y tipos — NO inventes nombres alternativos:",
            "",
        ]
        lines.extend(matches)
        lines.append("")
        return "\n".join(lines)

    except Exception:
        return ""


@executor(id="build_prompt")
async def build_prompt(
    input_json: str, ctx: WorkflowContext[AgentExecutorRequest]
) -> None:
    """Construye el prompt para UNA tabla y lo envía al ExecutorAgent.

    Espera un JSON con:
        {
          "table": { "table_name": "...", "table_description": "...",
                     "columns": [...] },
          "target_engine": "databricks_sql"
        }
    """
    try:
        data = json.loads(input_json)
    except json.JSONDecodeError:
        await ctx.send_message(
            AgentExecutorRequest(
                messages=[Message("user", contents=["Input inválido. Devuelve `{}`."])],
                should_respond=True,
            )
        )
        return

    table = data.get("table") or {}
    target_engine = data.get("target_engine") or "databricks_sql"
    table_name = table.get("table_name") or ""
    table_desc = table.get("table_description") or ""
    columns = table.get("columns") or []

    semantic_dict = await _build_semantic_dict(columns)

    parts: list[str] = []
    parts.append("## SOLICITUD")
    parts.append(f"Motor destino: **{target_engine}**")
    parts.append("")
    parts.append("## TABLA A MODELAR")
    if table_name:
        parts.append(f"Nombre provisional (Excel): `{table_name}`")
    if table_desc:
        parts.append(f"Definición funcional de la tabla: {table_desc}")
    parts.append("")
    parts.append(f"## COLUMNAS DEL INPUT ({len(columns)})")
    parts.append(_format_columns_block(columns))
    parts.append("")
    if semantic_dict:
        parts.append(semantic_dict)
    parts.append("## INSTRUCCIONES")
    parts.append(
        "0. Si existe el DICCIONARIO SEMANTICO MANDATORIO arriba, DEBES respetar esos nombres y tipos EXACTOS para las columnas correspondientes — no los omitas ni los renombres."
    )
    parts.append(
        "1. Llamá a `get_all_guidelines()` UNA SOLA VEZ para tener los lineamientos completos en contexto."
    )
    parts.append(
        "2. Decidí los nombres físicos según las reglas del system prompt, basándote 100 % en las **definiciones funcionales** (los nombres y tipos provisionales son solo orientativos)."
    )
    parts.append(
        "3. Devolvé EXCLUSIVAMENTE el JSON plano (sin wrapper `tables`) con `table_name`, `table_description`, `columns`, y `notes` opcional. Sin texto antes ni después."
    )
    parts.append(
        "4. NO incluyas audit columns, NO incluyas DDL, NO incluyas relaciones."
    )

    ctx.set_state("target_engine", target_engine)
    ctx.set_state("input_table_name", table_name)

    await ctx.send_message(
        AgentExecutorRequest(
            messages=[Message("user", contents=["\n".join(parts)])],
            should_respond=True,
        )
    )


@executor(id="emit_json")
async def emit_json(
    response: AgentExecutorResponse, ctx: WorkflowContext[Never, str]
) -> None:
    """Limpia la respuesta del LLM y la envía como output del workflow."""
    raw = response.agent_response.text
    engine = ctx.get_state("target_engine") or "databricks_sql"
    cleaned = _extract_json_from_text(raw)

    final = json.dumps(
        {
            "engine": engine,
            "table_json": cleaned,
        },
        ensure_ascii=False,
    )
    await ctx.yield_output(final)


def create_modeling_workflow(client: FoundryChatClient):
    """Construye el grafo: build_prompt → executor_agent → emit_json."""
    executor_agent = create_executor_agent(client)
    return (
        WorkflowBuilder(start_executor=build_prompt)
        .add_edge(build_prompt, executor_agent)
        .add_edge(executor_agent, emit_json)
        .build()
    )


async def run_table_pipeline(
    client: FoundryChatClient,
    table: dict,
    target_engine: str,
    *,
    session_id: str | None = None,
) -> dict:
    """Ejecuta el workflow para UNA tabla y devuelve el resultado parseado.

    Returns:
        Dict con forma `{"engine": "...", "table_json": "..."}` donde
        `table_json` es el string JSON crudo del LLM (a parsear por el
        caller). Si el workflow no produce salida, devuelve
        `{"error": ...}`.
    """
    from src.tools.knowledge_base_tools import session_scope

    workflow = create_modeling_workflow(client)
    payload = {"table": table, "target_engine": target_engine}
    input_json = json.dumps(payload, ensure_ascii=False)

    with session_scope(session_id):
        events = await workflow.run(input_json)
        outputs = events.get_outputs()

    if not outputs:
        return {"error": "El workflow no produjo resultados."}
    try:
        return json.loads(outputs[-1])
    except (json.JSONDecodeError, TypeError):
        return {"error": "Salida del workflow no es JSON válido.",
                "raw": str(outputs[-1])}
