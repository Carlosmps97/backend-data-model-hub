"""Factoría de agentes del sistema Data Modeler.

Crea y configura los agentes con sus respectivos clientes, tools e instrucciones.
Utiliza FoundryChatClient con autenticación de Service Principal.

IMPORTANTE — `max_tokens` (a.k.a `max_output_tokens` en la Responses API):
los agentes se crean con un techo ALTO y configurable de tokens de salida.
Sin esto, Azure aplica un default conservador (~4096) que TRUNCA el JSON
cuando una tabla tiene muchas columnas con descripciones largas. Síntoma
observado: tabla con 37 columnas devolvía solo 12 sin error visible.

El techo por defecto es 32000 tokens — deja margen para modelos con
ventanas de salida más grandes (gpt-4.1: 32K, gpt-5: 128K). El `max_tokens`
NO es un "mínimo": el modelo usa solo los tokens que necesita. Este valor
actúa como red de seguridad cuando el chunking por columnas no alcanza.

El chunking por sub-batches (ver `_run_one_table` en `api/routes/modeling.py`)
es la estrategia primaria para garantizar output completo independientemente
del cap de tokens.
"""

import os

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from azure.identity import ClientSecretCredential

from src.agents.instructions import (
    CONVERSATIONAL_AGENT_INSTRUCTIONS,
    EXECUTOR_AGENT_INSTRUCTIONS,
    QA_VALIDATOR_INSTRUCTIONS,
)
from src.config import settings
from src.tools.catalog_tools import (
    add_column_to_catalog,
    get_full_column_catalog,
    search_column_catalog,
)
from src.tools.excel_tools import parse_excel_file
from src.tools.convert_tools import convert_to_markdown
from src.tools.knowledge_base_tools import get_all_guidelines, query_guidelines


# Tope de tokens de salida que pasamos a la Responses API de Azure OpenAI.
# 32000 deja margen para modelos de ventana grande (gpt-4.1/gpt-5).
# gpt-4o hace cap efectivo en 16384, Azure ignora el exceso sin error.
# Es una red de seguridad; la estrategia principal es chunking por columnas.
# Configurable por env var.
_DEFAULT_MAX_OUTPUT_TOKENS = 32000
try:
    AGENT_MAX_OUTPUT_TOKENS: int = int(
        os.getenv("AGENT_MAX_OUTPUT_TOKENS") or _DEFAULT_MAX_OUTPUT_TOKENS
    )
except ValueError:
    AGENT_MAX_OUTPUT_TOKENS = _DEFAULT_MAX_OUTPUT_TOKENS
AGENT_MAX_OUTPUT_TOKENS = max(2048, AGENT_MAX_OUTPUT_TOKENS)

# Temperatura baja: el modelado de datos es una tarea estructural, no creativa.
# Reduce variabilidad turno a turno.
AGENT_TEMPERATURE: float = 0.1


def _get_credential() -> ClientSecretCredential:
    """Crea la credencial de Azure con Service Principal."""
    return ClientSecretCredential(
        tenant_id=settings.APP_AZURE_TENANT_ID,
        client_id=settings.APP_AZURE_CLIENT_ID,
        client_secret=settings.APP_AZURE_CLIENT_SECRET,
    )


def get_chat_client() -> FoundryChatClient:
    """Crea y retorna un FoundryChatClient configurado."""
    return FoundryChatClient(
        project_endpoint=settings.FOUNDRY_PROJECT_ENDPOINT,
        model=settings.FOUNDRY_MODEL,
        credential=_get_credential(),
    )


# ─── Default options compartidas por todos los agentes ──────────────────
#
# Pasar `max_tokens` aquí evita el truncamiento silencioso del JSON que se
# observaba al modelar tablas con muchas columnas (Azure default: ~4096).
# `temperature` baja garantiza salida determinista para una tarea estructural.

def _agent_default_options() -> dict:
    return {
        "max_tokens": AGENT_MAX_OUTPUT_TOKENS,
        "temperature": AGENT_TEMPERATURE,
    }


def create_executor_agent(client: FoundryChatClient | None = None) -> Agent:
    """Crea el agente Executor — genera modelos de datos y DDL.

    Tiene acceso a las tools de lineamientos para consultar naming conventions,
    tipos de dato por motor, y reglas generales de modelamiento.
    """
    if client is None:
        client = get_chat_client()

    return Agent(
        client=client,
        name="ExecutorAgent",
        instructions=EXECUTOR_AGENT_INSTRUCTIONS,
        tools=[query_guidelines, get_all_guidelines, convert_to_markdown],
        default_options=_agent_default_options(),
    )


def create_qa_agent(client: FoundryChatClient | None = None) -> Agent:
    """Crea el agente QA Validator — valida y estandariza modelos.

    Tiene acceso a las tools de catálogo de columnas y lineamientos
    para validación integral del modelo generado.
    """
    if client is None:
        client = get_chat_client()

    return Agent(
        client=client,
        name="QAValidatorAgent",
        instructions=QA_VALIDATOR_INSTRUCTIONS,
        tools=[
            query_guidelines,
            search_column_catalog,
            add_column_to_catalog,
            get_full_column_catalog,
            convert_to_markdown,
        ],
        default_options=_agent_default_options(),
    )


def create_conversational_agent(client: FoundryChatClient | None = None) -> Agent:
    """Crea el agente Conversacional — orquestador y UI.

    Tiene acceso a la tool de parsing de Excel para procesar
    archivos del usuario.
    """
    if client is None:
        client = get_chat_client()

    return Agent(
        client=client,
        name="ConversationalAgent",
        instructions=CONVERSATIONAL_AGENT_INSTRUCTIONS,
        tools=[parse_excel_file],
        default_options=_agent_default_options(),
    )
