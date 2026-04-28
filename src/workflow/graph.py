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


def _format_history_block(history: list[dict[str, str]]) -> str:
    """Formatea el historial de mensajes para incluirlo en el prompt.

    El historial es una lista de dicts {role, content}. Cada turno se rotula
    como "Usuario:" o "Agente:" para que el ExecutorAgent reconozca el flujo.
    """
    if not history:
        return ""

    lines: list[str] = []
    for msg in history:
        role = msg.get("role", "")
        content = msg.get("content", "")
        if not content:
            continue
        label = "Usuario" if role == "user" else "Agente"
        lines.append(f"**{label}:** {content}")
    return "\n\n".join(lines)


def _format_last_model_block(last_model: dict) -> str:
    """Serializa el último modelo generado para inyectarlo como contexto.

    Se entrega en JSON compacto pero legible para que el agente lo tome como
    estado base. NO se inventan reglas: solo se transmite el contenido.
    """
    if not last_model:
        return ""
    try:
        return json.dumps(last_model, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(last_model)


@executor(id="prepare_input")
async def prepare_input(
    input_json: str, ctx: WorkflowContext[AgentExecutorRequest]
) -> None:
    """Prepara el input del usuario para el ExecutorAgent.

    Recibe un JSON con las tablas parseadas, texto del usuario, motor destino,
    relaciones y opcionalmente `history` (lista de mensajes previos) y
    `last_model` (último modelo generado en la conversación). Construye un
    prompt comprehensivo para el agente generador.
    """
    try:
        data = json.loads(input_json)
    except json.JSONDecodeError:
        data = {"user_text": input_json, "tables": [], "target_engine": "databricks_sql", "relationships": []}

    tables = data.get("tables", [])
    user_text = data.get("user_text", "")
    engine = data.get("target_engine", "databricks_sql")
    relationships = data.get("relationships", [])
    history = data.get("history", []) or []
    last_model = data.get("last_model") or {}

    # Construir prompt para el ExecutorAgent
    prompt_parts: list[str] = []

    # ── Contexto previo de la conversación (si existe) ──────────────────
    history_block = _format_history_block(history)
    if history_block:
        prompt_parts.append("## HISTORIAL DE CONVERSACIÓN PREVIA")
        prompt_parts.append(
            "Mensajes intercambiados antes de la solicitud actual. "
            "Úsalos para mantener continuidad y aplicar refinamientos sobre "
            "lo ya modelado, no para regenerar desde cero."
        )
        prompt_parts.append(history_block)
        prompt_parts.append("")

    last_model_block = _format_last_model_block(last_model)
    if last_model_block:
        prompt_parts.append("## MODELO ACTUAL")
        prompt_parts.append(
            "Este es el último modelo generado en esta conversación. "
            "Tómalo como base. Aplica únicamente los cambios o refinamientos "
            "que pida la solicitud actual sin descartar tablas o columnas "
            "previas, salvo que el usuario lo solicite explícitamente."
        )
        prompt_parts.append("```json")
        prompt_parts.append(last_model_block)
        prompt_parts.append("```")
        prompt_parts.append("")

    # ── Solicitud actual ────────────────────────────────────────────────
    prompt_parts.append("## SOLICITUD DE MODELAMIENTO DE DATOS")
    prompt_parts.append(f"Motor de base de datos destino: **{engine}**")

    if user_text:
        prompt_parts.append(f"\n## CONTEXTO DEL USUARIO\n{user_text}")

    if tables:
        total_columns = sum(len(t.get("columns", [])) for t in tables)
        any_proposed = any(t.get("is_proposed_name", False) for t in tables)
        has_descriptions = any(t.get("table_description") for t in tables)

        prompt_parts.append(
            f"\n## TABLAS A MODELAR ({len(tables)} tabla(s), {total_columns} columna(s) en total)"
        )

        if any_proposed:
            prompt_parts.append(
                "> ⚠️ **NOMBRES PROVISIONALES**: Los nombres de tablas y columnas que aparecen a "
                "continuación provienen del Excel del usuario y son SUGERENCIAS PRELIMINARES, "
                "no los nombres físicos finales. El agente DEBE derivar los nombres finales "
                "aplicando estrictamente las convenciones de nomenclatura de los lineamientos "
                "(prefijos por dominio, abreviaturas del glosario, estilo UPPERCASE_SNAKE, etc.). "
                "Los tipos de dato son INDICACIONES; el tipo final se determina por el "
                "Parent Domain que establezcan los lineamientos."
            )
            if has_descriptions:
                prompt_parts.append(
                    "> La **descripción funcional de cada tabla** (cuando está presente) proviene "
                    "de la pestaña `TableDefinition` del Excel y representa el significado de "
                    "negocio. Usarla para entender el dominio y aplicar la nomenclatura correcta."
                )

        for tidx, table in enumerate(tables, 1):
            tname = table.get("table_name", "sin_nombre")
            tdesc = table.get("table_description", "")
            is_proposed = table.get("is_proposed_name", False)
            cols = table.get("columns", [])

            if is_proposed:
                header = (
                    f"\n### Tabla {tidx}: nombre provisional en Excel = \"{tname}\" "
                    f"({len(cols)} columna(s))"
                )
            else:
                header = f"\n### Tabla {tidx}: {tname} ({len(cols)} columna(s))"
            prompt_parts.append(header)

            if tdesc:
                prompt_parts.append(f"**Descripción funcional de la tabla:** {tdesc}")

            if is_proposed:
                prompt_parts.append(
                    "_El nombre físico final debe construirse según los lineamientos "
                    "(consultar `get_all_guidelines()`)._"
                )

            for idx, col in enumerate(cols, 1):
                col_name = col.get("column_name", "")
                func_def = col.get("functional_definition", "")
                dtype = col.get("data_type_hint", "")
                nullable = col.get("is_nullable", "")
                notes = col.get("notes", "")

                # Formato compacto de una línea por columna para no exceder
                # el contexto del LLM con modelos grandes (muchas columnas)
                line_parts: list[str] = [f"{idx}."]
                if col_name:
                    line_parts.append(f"`{col_name}`")
                if dtype:
                    line_parts.append(f"({dtype})")
                if func_def:
                    line_parts.append(f"— {func_def}")
                if nullable != "" and nullable is not False:
                    line_parts.append("[nullable]")
                if notes:
                    line_parts.append(f"[nota: {notes}]")

                prompt_parts.append("   " + " ".join(line_parts))

    if relationships:
        prompt_parts.append("\n## RELACIONES ENTRE TABLAS")
        for rel in relationships:
            prompt_parts.append(f"- {rel}")

    total_columns_val = sum(len(t.get("columns", [])) for t in tables) if tables else 0
    any_proposed_flag = any(t.get("is_proposed_name", False) for t in tables) if tables else False

    instrucciones = [
        "\n## INSTRUCCIONES",
        "1. Llama a `get_all_guidelines()` para obtener los lineamientos corporativos COMPLETOS.",
        "2. Analiza los lineamientos: nomenclatura de tablas, prefijos por dominio/solución, "
           "abreviaturas del glosario, Parent Domain por tipo de dato, columnas técnicas obligatorias.",
        "3. Genera el modelo de datos aplicando ESTRICTAMENTE los lineamientos obtenidos.",
        f"4. **FIDELIDAD**: El modelo DEBE incluir TODAS las {total_columns_val} columna(s) "
           "solicitadas por el usuario, más las columnas técnicas obligatorias de los lineamientos. "
           "No omitas ninguna columna del usuario.",
    ]

    if any_proposed_flag:
        instrucciones.append(
            "5. **NOMBRES PROVISIONALES**: Tabla y columna en el Excel son SUGERENCIAS. "
            "Deriva los nombres físicos finales usando: prefijos de los lineamientos (HD_/DM_/etc.), "
            "glosario de abreviaturas, UPPERCASE_SNAKE_CASE. El tipo final lo da el Parent Domain "
            "del lineamiento (monto→DECIMAL(21,4), codigo→VARCHAR(20), fecha→DATE, etc.). "
            "La descripción funcional de cada tabla (TableDefinition) indica el dominio/prefijo correcto."
        )
        instrucciones.append(
            "6. **PROCESA ABSOLUTAMENTE TODAS las tablas del input** — no omitas ninguna. "
            "Si el modelo ACTUAL existe, pártelo como base y aplica solo los cambios."
        )
        instrucciones.append("7. Genera DDL ejecutable para el motor destino.")
        instrucciones.append("8. Responde EXCLUSIVAMENTE en formato JSON válido.")
    else:
        instrucciones.extend([
            "5. Si existe MODELO ACTUAL, pártelo como base y aplica solo los cambios pedidos.",
            "6. **PROCESA TODAS las tablas del input** — no omitas ninguna.",
            "7. Genera DDL ejecutable para el motor destino.",
            "8. Responde EXCLUSIVAMENTE en formato JSON válido.",
        ])

    prompt_parts.append("\n".join(instrucciones))

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


# ─── Modo "lean": ExecutorAgent + format, SIN QA ───────────────────────


@executor(id="format_lean_output")
async def format_lean_output(
    response: AgentExecutorResponse, ctx: WorkflowContext[Never, str]
) -> None:
    """Salida del workflow lean (Executor sin QA).

    Empaqueta la respuesta del ExecutorAgent en el mismo contrato que
    `format_output` produce: un JSON con `engine`, `generated_model` y
    `qa_validation` (vacía aquí). El `response_builder.py` ya tolera
    `qa_validation` vacío y usa `tables` del modelo generado.
    """
    model_text = response.agent_response.text
    engine = ctx.get_state("target_engine") or "databricks_sql"

    clean_model = _extract_json_from_text(model_text)

    final_result = json.dumps(
        {
            "engine": engine,
            "generated_model": clean_model,
            "qa_validation": "{}",
        },
        ensure_ascii=False,
    )
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


def create_lean_workflow(client: FoundryChatClient):
    """Crea un workflow reducido (sin QA) para procesamiento por tabla.

    Grafo: prepare_input → executor_agent → format_lean_output

    Útil cuando se procesa una tabla por turno en modo per-table: el
    QAValidatorAgent deja de aportar valor (las columnas son las del
    Excel del usuario y el ExecutorAgent ya consultó las guidelines)
    y se transforma en un duplicado de trabajo + costo de tokens + RPM.

    Saltarse el QA en este modo:
    - reduce ~50% las llamadas al LLM por tabla,
    - elimina el bucle de N llamadas a `search_column_catalog` por
      tabla (37 búsquedas para una tabla con 37 columnas), que es la
      principal causa de rate limit.
    """
    executor_agent = create_executor_agent(client)

    workflow = (
        WorkflowBuilder(start_executor=prepare_input)
        .add_edge(prepare_input, executor_agent)
        .add_edge(executor_agent, format_lean_output)
        .build()
    )

    return workflow


async def run_modeling_pipeline(
    client: FoundryChatClient,
    input_data: dict,
    *,
    lean: bool = False,
) -> dict:
    """Ejecuta el pipeline de modelamiento.

    Args:
        client: FoundryChatClient configurado.
        input_data: Dict con tables, user_text, target_engine, relationships y,
            opcionalmente, history (lista de mensajes previos), last_model
            (último modelo generado en la conversación) y session_id (id de
            la conversación, usado para resolver guidelines por sesión).
        lean: si True, usa el grafo reducido (sin QAValidatorAgent). Default
            False → grafo completo con QA.

    Returns:
        Dict con el resultado del pipeline. La forma del dict es la misma
        en modo lean y full; en lean el `qa_validation` es `"{}"`.
    """
    from src.tools.knowledge_base_tools import session_scope

    workflow = create_lean_workflow(client) if lean else create_modeling_workflow(client)

    # No serializamos session_id al prompt; solo se usa para fijar el cache
    # de guidelines aplicable durante esta ejecución.
    payload = {k: v for k, v in input_data.items() if k != "session_id"}
    input_json = json.dumps(payload, ensure_ascii=False)

    session_id = input_data.get("session_id")

    with session_scope(session_id):
        events = await workflow.run(input_json)
        outputs = events.get_outputs()

    if outputs:
        try:
            return json.loads(outputs[-1])
        except (json.JSONDecodeError, TypeError):
            return {"raw_output": str(outputs[-1])}

    return {"error": "El workflow no produjo resultados."}
