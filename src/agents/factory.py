"""Factoría del único agente del sistema: ExecutorAgent.

ExecutorAgent recibe UNA tabla con su definición funcional y devuelve
los nombres físicos según los lineamientos. Es la ÚNICA pieza con LLM
en el pipeline; todo lo demás (parseo de Excel, audit columns, DDL,
markdown) es código Python determinista.

Notas de configuración:
- `max_tokens` queda en 8000. El output de UNA tabla, sin DDL ni
  audit columns, es del orden de 200-500 tokens. 8K deja margen sobrado
  para cualquier tabla razonable y mantiene rate limit de output bajo.
- `temperature=0.1`: tarea estructural, no creativa.
"""

from __future__ import annotations

import os

from agent_framework import Agent
from agent_framework.foundry import FoundryChatClient
from azure.identity import ClientSecretCredential

from src.agents.instructions import EXECUTOR_AGENT_INSTRUCTIONS
from src.config import settings
from src.tools.knowledge_base_tools import (
    get_all_guidelines,
    query_guidelines,
)


_DEFAULT_MAX_OUTPUT_TOKENS = 8000
try:
    AGENT_MAX_OUTPUT_TOKENS: int = int(
        os.getenv("AGENT_MAX_OUTPUT_TOKENS") or _DEFAULT_MAX_OUTPUT_TOKENS
    )
except ValueError:
    AGENT_MAX_OUTPUT_TOKENS = _DEFAULT_MAX_OUTPUT_TOKENS
AGENT_MAX_OUTPUT_TOKENS = max(2048, AGENT_MAX_OUTPUT_TOKENS)

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


def _agent_default_options() -> dict:
    return {
        "max_tokens": AGENT_MAX_OUTPUT_TOKENS,
        "temperature": AGENT_TEMPERATURE,
    }


def create_executor_agent(client: FoundryChatClient | None = None) -> Agent:
    """Crea el ExecutorAgent — único agente del sistema.

    Tools: solo las dos de knowledge_base. El agente lee los lineamientos
    y produce los nombres físicos. No resuelve prefijos via tools custom
    (los lineamientos en formato JSON ya son auto-suficientes).
    """
    if client is None:
        client = get_chat_client()

    return Agent(
        client=client,
        name="ExecutorAgent",
        instructions=EXECUTOR_AGENT_INSTRUCTIONS,
        tools=[get_all_guidelines, query_guidelines],
        default_options=_agent_default_options(),
    )
