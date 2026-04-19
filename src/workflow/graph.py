"""Definición del workflow (grafo dirigido) del pipeline de modelamiento.

Implementa el grafo:
  prepare_input → executor_agent → extract_model → qa_agent → format_output

Usa WorkflowBuilder del Microsoft Agent Framework con executors custom
y agentes integrados al grafo.
"""

import json
import re
from typing import cast

from agent_framework import (
    Agent,
    AgentExecutorRequest,
    AgentExecutorResponse,
    Message,
    WorkflowBuilder,
    WorkflowContext,
    executor,
)
from agent_framework.foundry import FoundryChatClient
from typing_extensions import Never

from src.agents.factory import create_executor_agent, create_qa_agent


def _fix_trailing_commas(text: str) -> str:
    """Elimina trailing commas antes de } o ] que el LLM a veces genera."""
    return re.sub(r',\s*([}\]])', r'\1', text)


def _extract_json_from_text(text: str) -> str:
    """Extrae JSON válido de una respuesta que puede contener texto adicional.

    Maneja casos donde el LLM responde con texto preamble + JSON en markdown fences.
    Limpia trailing commas y otros artefactos comunes del LLM.
    """
    if not text:
        return "{}"

    t = text.strip()

    def _try_parse(candidate: str) -> str | None:
        """Intenta parsear JSON, con y sin fix de trailing commas."""
        for attempt in [candidate, _fix_trailing_commas(candidate)]:
            try:
                json.loads(attempt)
                return attempt
            except json.JSONDecodeError:
                continue
        return None

    # Caso 1: JSON en fences ```json ... ```
    fence_match = re.search(r'```(?:json)?\s*\n?(\{.*\})\s*```', t, re.DOTALL)
    if fence_match:
        candidate = fence_match.group(1).strip()
        result = _try_parse(candidate)
        if result:
            return result
        return _fix_trailing_commas(candidate)

    # Caso 2: JSON puro (empieza con {)
    if t.startswith('{'):
        result = _try_parse(t)
        if result:
            return result
        # Encontrar el último } balanceado
        depth = 0
        end = -1
        for i, c in enumerate(t):
            if c == '{':
                depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0:
                    end = i
        if end > 0:
            candidate = t[:end + 1]
            result = _try_parse(candidate)
            return result or _fix_trailing_commas(candidate)
        return _fix_trailing_commas(t)

    # Caso 3: Buscar primer { y último } en el texto
    first_brace = t.find('{')
    last_brace = t.rfind('}')
    if first_brace >= 0 and last_brace > first_brace:
        candidate = t[first_brace:last_brace + 1]
        result = _try_parse(candidate)
        if result:
            return result
        return _fix_trailing_commas(candidate)

    return t


@executor(id="prepare_input")
async def prepare_input(
    input_json: str, ctx: WorkflowContext[AgentExecutorRequest]
) -> None:
    """Prepara el input del usuario para el ExecutorAgent.

    Recibe un JSON con las tablas parseadas, texto del usuario, motor destino
    y relaciones. Construye un prompt comprehensivo para el agente generador.
    """
    try:
        data = json.loads(input_json)
    except json.JSONDecodeError:
        data = {"user_text": input_json, "tables": [], "target_engine": "databricks_sql", "relationships": []}

    tables = data.get("tables", [])
    user_text = data.get("user_text", "")
    engine = data.get("target_engine", "databricks_sql")
    relationships = data.get("relationships", [])

    # Construir prompt para el ExecutorAgent
    prompt_parts = [
        f"## SOLICITUD DE MODELAMIENTO DE DATOS",
        f"Motor de base de datos destino: **{engine}**",
    ]

    if user_text:
        prompt_parts.append(f"\n## CONTEXTO DEL USUARIO\n{user_text}")

    if tables:
        total_columns = sum(len(t.get("columns", [])) for t in tables)
        prompt_parts.append(f"\n## TABLAS A MODELAR ({len(tables)} tabla(s), {total_columns} columna(s) en total)")
        for table in tables:
            tname = table.get("table_name", "sin_nombre")
            cols = table.get("columns", [])
            prompt_parts.append(f"\n### Tabla: {tname} ({len(cols)} columnas)")
            for idx, col in enumerate(cols, 1):
                col_name = col.get("column_name", "(sin nombre)")
                func_def = col.get("functional_definition", "")
                dtype = col.get("data_type_hint", "")
                nullable = col.get("is_nullable", "")
                notes = col.get("notes", "")
                parts = [f"{idx}. **{col_name}**"]
                if func_def:
                    parts.append(f"Definición: {func_def}")
                if dtype:
                    parts.append(f"Tipo sugerido: {dtype}")
                if nullable != "":
                    parts.append(f"Nullable: {nullable}")
                if notes:
                    parts.append(f"Notas: {notes}")
                prompt_parts.append("   " + " | ".join(parts))

    if relationships:
        prompt_parts.append("\n## RELACIONES ENTRE TABLAS")
        for rel in relationships:
            prompt_parts.append(f"- {rel}")

    prompt_parts.append(
        "\n## INSTRUCCIONES"
        "\n1. Llama a `get_all_guidelines()` para obtener los lineamientos corporativos COMPLETOS."
        "\n2. Analiza los lineamientos: nomenclatura de tablas, prefijos de columnas, tipos de dato, columnas obligatorias."
        "\n3. Genera el modelo de datos aplicando estrictamente los lineamientos obtenidos."
        f"\n4. **FIDELIDAD**: El modelo DEBE incluir TODAS las {total_columns if tables else 0} columna(s) solicitadas por el usuario, más las columnas obligatorias de los lineamientos. No omitas ninguna."
        "\n5. Genera DDL ejecutable para el motor destino."
        "\n6. Responde EXCLUSIVAMENTE en formato JSON válido según tu formato de respuesta."
    )

    prompt = "\n".join(prompt_parts)

    # Guardar datos en estado del workflow para uso posterior
    ctx.set_state("input_data", data)
    ctx.set_state("target_engine", engine)

    await ctx.send_message(
        AgentExecutorRequest(
            messages=[Message("user", contents=[prompt])],
            should_respond=True,
        )
    )


@executor(id="extract_model")
async def extract_model(
    response: AgentExecutorResponse, ctx: WorkflowContext[AgentExecutorRequest]
) -> None:
    """Extrae el modelo generado por el ExecutorAgent y prepara el input para QA.

    Toma la respuesta del ExecutorAgent, la almacena en el estado del workflow,
    y construye el prompt para el QAValidatorAgent.
    """
    model_text = response.agent_response.text
    engine = ctx.get_state("target_engine") or "databricks_sql"

    # Guardar modelo en estado
    ctx.set_state("generated_model", model_text)

    # Construir prompt para QA Agent
    qa_prompt = f"""## VALIDACIÓN DE MODELO DE DATOS

## MODELO GENERADO POR EL EXECUTOR AGENT
{model_text}

## MOTOR DE BASE DE DATOS
{engine}

## INSTRUCCIONES DE VALIDACIÓN
1. Consulta los lineamientos con query_guidelines para verificar cumplimiento.
2. Para CADA columna del modelo, llama a search_column_catalog con su definición funcional.
3. Si el catálogo tiene una columna con la misma definición funcional (similitud >= 0.5),
   REEMPLAZA el nombre por el estandarizado del catálogo.
4. Para columnas nuevas sin match en el catálogo, lláma a add_column_to_catalog.
5. Si corregiste nombres, regenera el DDL completo.
6. Calcula un quality_score (0-100).
7. Responde EXCLUSIVAMENTE en formato JSON válido según tu formato de respuesta.
"""

    await ctx.send_message(
        AgentExecutorRequest(
            messages=[Message("user", contents=[qa_prompt])],
            should_respond=True,
        )
    )


@executor(id="format_output")
async def format_output(
    response: AgentExecutorResponse, ctx: WorkflowContext[Never, str]
) -> None:
    """Formatea la salida final del pipeline de modelamiento.

    Combina el modelo original, las validaciones del QA y genera
    la respuesta final del workflow.
    """
    qa_result = response.agent_response.text
    generated_model = ctx.get_state("generated_model") or ""
    engine = ctx.get_state("target_engine") or "databricks_sql"

    # Limpiar JSON de ambas respuestas (quitar fences, preamble)
    clean_model = _extract_json_from_text(generated_model)
    clean_qa = _extract_json_from_text(qa_result)

    # Construir resultado final como JSON
    final_result = json.dumps({
        "engine": engine,
        "generated_model": clean_model,
        "qa_validation": clean_qa,
    }, ensure_ascii=False)

    await ctx.yield_output(final_result)


def create_modeling_workflow(client: FoundryChatClient):
    """Crea el workflow completo de modelamiento de datos.

    Grafo: prepare_input → executor_agent → extract_model → qa_agent → format_output

    Args:
        client: FoundryChatClient configurado para crear los agentes.

    Returns:
        Workflow construido y listo para ejecutar.
    """
    executor_agent = create_executor_agent(client)
    qa_agent = create_qa_agent(client)

    workflow = (
        WorkflowBuilder(start_executor=prepare_input)
        .add_edge(prepare_input, executor_agent)
        .add_edge(executor_agent, extract_model)
        .add_edge(extract_model, qa_agent)
        .add_edge(qa_agent, format_output)
        .build()
    )

    return workflow


async def run_modeling_pipeline(
    client: FoundryChatClient,
    input_data: dict,
) -> dict:
    """Ejecuta el pipeline completo de modelamiento.

    Args:
        client: FoundryChatClient configurado.
        input_data: Dict con tables, user_text, target_engine, relationships.

    Returns:
        Dict con el resultado completo del pipeline.
    """
    workflow = create_modeling_workflow(client)
    input_json = json.dumps(input_data, ensure_ascii=False)

    events = await workflow.run(input_json)
    outputs = events.get_outputs()

    if outputs:
        try:
            return json.loads(outputs[-1])
        except (json.JSONDecodeError, TypeError):
            return {"raw_output": str(outputs[-1])}

    return {"error": "El workflow no produjo resultados."}
