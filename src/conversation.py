"""Almacén en memoria de conversaciones del Data Modeler Agent.

Mantiene el estado por `conversation_id` (UUID generado por el frontend):
historial de mensajes, guidelines cargadas para esa sesión, motor de BD
activo y último modelo generado.

Diseño:
- Diccionario en memoria, sin persistencia (consigna explícita).
- Un `asyncio.Lock` por conversación para serializar mutaciones por sesión
  sin bloquear el resto del store.
- Un `asyncio.Lock` global solo para crear/borrar entradas del diccionario.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class GuidelinesMeta:
    """Metadatos de las guidelines cargadas para una conversación."""

    file_name: str = ""
    file_format: str = ""
    file_size_bytes: int = 0
    preview: str = ""


@dataclass
class ConversationState:
    """Estado completo de una conversación."""

    conversation_id: str
    engine: str
    history: list[dict[str, str]] = field(default_factory=list)
    guidelines_meta: GuidelinesMeta | None = None
    last_model: dict[str, Any] | None = None
    turn_number: int = 0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def touch(self) -> None:
        self.updated_at = time.time()


class ConversationStore:
    """Store en memoria de conversaciones.

    Todas las operaciones que mutan estado de una conversación específica
    deben hacerse bajo el lock de esa conversación (`async with state.lock`).
    Las operaciones que mutan el diccionario raíz usan `_root_lock`.
    """

    def __init__(self) -> None:
        self._conversations: dict[str, ConversationState] = {}
        self._root_lock: asyncio.Lock = asyncio.Lock()

    async def get_or_create(
        self, conversation_id: str, engine: str
    ) -> ConversationState:
        """Crea una conversación si no existe; si existe, actualiza el engine."""
        async with self._root_lock:
            state = self._conversations.get(conversation_id)
            if state is None:
                state = ConversationState(
                    conversation_id=conversation_id,
                    engine=engine,
                )
                self._conversations[conversation_id] = state
            return state

    async def get(self, conversation_id: str) -> ConversationState | None:
        """Retorna la conversación o None si no existe."""
        async with self._root_lock:
            return self._conversations.get(conversation_id)

    async def exists(self, conversation_id: str) -> bool:
        """Indica si una conversación existe en el store."""
        async with self._root_lock:
            return conversation_id in self._conversations

    async def append_message(
        self, conversation_id: str, role: str, content: str
    ) -> None:
        """Agrega un mensaje al historial. La conversación debe existir."""
        state = await self._require(conversation_id)
        async with state.lock:
            state.history.append({"role": role, "content": content})
            state.touch()

    async def get_history(self, conversation_id: str) -> list[dict[str, str]]:
        """Retorna una copia del historial de mensajes."""
        state = await self._require(conversation_id)
        async with state.lock:
            return list(state.history)

    async def set_guidelines(
        self,
        conversation_id: str,
        meta: GuidelinesMeta,
    ) -> None:
        """Registra los metadatos de las guidelines cargadas para la sesión.

        El contenido procesado de las guidelines se almacena en
        `src.tools.knowledge_base_tools._guidelines_cache_by_session` (ver
        Mejora 2). El store solo guarda metadatos consultables por la API.
        """
        state = await self._require(conversation_id)
        async with state.lock:
            state.guidelines_meta = meta
            state.touch()

    async def get_guidelines(
        self, conversation_id: str
    ) -> GuidelinesMeta | None:
        """Retorna los metadatos de las guidelines de la sesión, si existen."""
        state = await self._require(conversation_id)
        async with state.lock:
            return state.guidelines_meta

    async def set_last_model(
        self, conversation_id: str, model: dict[str, Any]
    ) -> None:
        """Guarda el último modelo generado e incrementa el `turn_number`."""
        state = await self._require(conversation_id)
        async with state.lock:
            state.last_model = model
            state.turn_number += 1
            state.touch()

    async def get_last_model(
        self, conversation_id: str
    ) -> dict[str, Any] | None:
        """Retorna el último modelo generado en la sesión."""
        state = await self._require(conversation_id)
        async with state.lock:
            return state.last_model

    async def get_turn_number(self, conversation_id: str) -> int:
        """Retorna el `turn_number` actual de la conversación."""
        state = await self._require(conversation_id)
        async with state.lock:
            return state.turn_number

    async def set_engine(self, conversation_id: str, engine: str) -> None:
        """Cambia el motor de BD activo para la conversación."""
        state = await self._require(conversation_id)
        async with state.lock:
            state.engine = engine
            state.touch()

    async def get_engine(self, conversation_id: str) -> str:
        """Retorna el motor de BD activo para la conversación."""
        state = await self._require(conversation_id)
        async with state.lock:
            return state.engine

    async def clear(self, conversation_id: str) -> bool:
        """Elimina la conversación del store y libera su memoria.

        Retorna True si la conversación existía, False en caso contrario.
        """
        async with self._root_lock:
            state = self._conversations.pop(conversation_id, None)
        if state is None:
            return False
        # Limpiar también el cache de guidelines de la sesión.
        # Importación tardía para evitar ciclos.
        from src.tools.knowledge_base_tools import clear_session_guidelines

        clear_session_guidelines(conversation_id)
        return True

    async def list_ids(self) -> list[str]:
        """Lista todos los `conversation_id` activos. Útil para debugging."""
        async with self._root_lock:
            return list(self._conversations.keys())

    async def _require(self, conversation_id: str) -> ConversationState:
        """Obtiene la conversación o lanza KeyError si no existe."""
        async with self._root_lock:
            state = self._conversations.get(conversation_id)
        if state is None:
            raise KeyError(
                f"Conversation '{conversation_id}' not found in store."
            )
        return state
