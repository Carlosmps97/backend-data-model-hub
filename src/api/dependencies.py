"""Dependencias compartidas de la capa FastAPI.

Patrón:
- El `lifespan` en `api/main.py` crea los singletons del proceso
  (ConversationStore, FoundryChatClient, asyncio.Semaphore) y los guarda en
  `app.state`.
- Estas dependencias los recuperan vía `Request` y los entregan a los
  endpoints, con validación adicional cuando aplica (ej. `require_conversation`
  responde 404 si la conversación no existe).

Nada de lógica de negocio aquí: solo wiring.
"""

from __future__ import annotations

import asyncio
import re
from typing import Annotated

from fastapi import Depends, HTTPException, Path, Request, status

from src.conversation import ConversationState, ConversationStore


# ─── UUID v4 estricto ───────────────────────────────────────────────────

_UUID_V4_RE = re.compile(
    r"^[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"4[0-9a-fA-F]{3}-"
    r"[89abAB][0-9a-fA-F]{3}-"
    r"[0-9a-fA-F]{12}$"
)


def validate_uuid_v4(value: str) -> str:
    """Valida que el string sea un UUID v4. Lanza 400 si no lo es."""
    if not _UUID_V4_RE.match(value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"conversation_id no es un UUID v4 válido: {value}",
        )
    return value


# ─── Acceso a singletons del proceso ────────────────────────────────────


def get_store(request: Request) -> ConversationStore:
    """Provee el ConversationStore singleton del proceso."""
    store = getattr(request.app.state, "store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="ConversationStore no inicializado.",
        )
    return store


def get_chat_client(request: Request):
    """Provee el FoundryChatClient compartido del proceso.

    Levanta 503 si el cliente no se pudo inicializar al arranque (credenciales
    Azure faltantes / endpoint inválido).
    """
    client = getattr(request.app.state, "chat_client", None)
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Azure AI Foundry no está conectado. Verificar configuración.",
        )
    return client


def get_pipeline_semaphore(request: Request) -> asyncio.Semaphore:
    """Provee el semaphore que limita ejecuciones concurrentes del pipeline."""
    sem = getattr(request.app.state, "pipeline_semaphore", None)
    if sem is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Semaphore de pipeline no inicializado.",
        )
    return sem


# ─── Validación de ruta: conversation_id ────────────────────────────────


async def require_conversation(
    conversation_id: Annotated[
        str,
        Path(description="UUID v4 de la conversación."),
    ],
    store: Annotated[ConversationStore, Depends(get_store)],
) -> ConversationState:
    """Valida UUID v4 y existencia. Devuelve la `ConversationState`.

    - 400 si el path param no es UUID v4.
    - 404 si no existe en el store.
    """
    validate_uuid_v4(conversation_id)
    state = await store.get(conversation_id)
    if state is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"Conversación '{conversation_id}' no encontrada. "
                "Crea primero la conversación con POST /api/conversations."
            ),
        )
    return state


# Aliases tipados para usar en los routers como Depends().
StoreDep = Annotated[ConversationStore, Depends(get_store)]
ChatClientDep = Annotated[object, Depends(get_chat_client)]
PipelineSemaphoreDep = Annotated[asyncio.Semaphore, Depends(get_pipeline_semaphore)]
ConversationDep = Annotated[ConversationState, Depends(require_conversation)]
