"""Factoría de agentes del sistema Data Modeler.

Crea y configura los agentes con sus respectivos clientes, tools e instrucciones.
Utiliza FoundryChatClient con autenticación de Service Principal.
"""

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
from src.tools.knowledge_base_tools import get_all_guidelines, query_guidelines


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
        tools=[query_guidelines, get_all_guidelines],
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
        ],
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
    )
