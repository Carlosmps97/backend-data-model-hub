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
from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request, Path, status

from src.api.auth import AUTH_COOKIE_NAME, verify_token
from src.conversation import ConversationState, ConversationStore
from src.db.db_models import UserDoc
from src.db.users_db import get_user_by_id


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


# ─── Helpers de permisos ─────────────────────────────────────────────────
# Mirrors web-data-model-hub/src/lib/auth/permissions.ts


def project_access_level(user: UserDoc, project_id: str) -> Literal["view", "edit"] | None:
    """Returns the best access level the user has on a project, or None."""
    if user.role == "admin":
        return "edit"
    best: Literal["view", "edit"] | None = None
    for perm in user.permissions:
        if perm.projectId == project_id:  # matches both project- and model-scope
            if perm.level == "edit":
                return "edit"
            if best is None:
                best = "view"
    return best


def model_access_level(
    user: UserDoc, model_id: str, project_id: str,
) -> Literal["view", "edit"] | None:
    """Returns the user's effective access level on a specific model.

    A model-scope grant matching the `modelId` wins. Otherwise, any
    project-scope grant on its `projectId` applies.
    """
    if user.role == "admin":
        return "edit"
    best: Literal["view", "edit"] | None = None
    for perm in user.permissions:
        matches = (
            (perm.scope == "model" and perm.modelId == model_id)
            or (perm.scope == "project" and perm.projectId == project_id)
        )
        if matches:
            if perm.level == "edit":
                return "edit"
            if best is None:
                best = "view"
    return best


def is_admin(user: UserDoc) -> bool:
    return user.role == "admin"


def can_view_project(user: UserDoc, project_id: str) -> bool:
    return project_access_level(user, project_id) is not None


def can_edit_project(user: UserDoc, project_id: str) -> bool:
    return project_access_level(user, project_id) == "edit"


def can_view_model(user: UserDoc, model_id: str, project_id: str) -> bool:
    return model_access_level(user, model_id, project_id) is not None


def can_edit_model(user: UserDoc, model_id: str, project_id: str) -> bool:
    return model_access_level(user, model_id, project_id) == "edit"


def visible_project_ids(user: UserDoc) -> set[str]:
    return {p.projectId for p in user.permissions}


# ─── Auth dependencies ───────────────────────────────────────────────────


async def get_current_user(request: Request) -> UserDoc | None:
    """Reads the session cookie, verifies JWT, reloads user from DB.

    Always re-fetches from DB so permission/status changes mid-session
    take effect immediately (same contract as the TypeScript layer).
    """
    token = request.cookies.get(AUTH_COOKIE_NAME)
    if not token:
        return None
    claims = verify_token(token)
    if not claims:
        return None
    return await get_user_by_id(claims["sub"])


async def require_user(
    user: Annotated[UserDoc | None, Depends(get_current_user)],
) -> UserDoc:
    """Raises 401 if the request has no valid session or the user is inactive."""
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
        )
    if not user.isActive:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="This user is disabled.",
        )
    return user


async def require_admin(
    user: Annotated[UserDoc, Depends(require_user)],
) -> UserDoc:
    """Raises 403 if the authenticated user is not an admin."""
    if not is_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required.",
        )
    return user


async def check_project_access(user: UserDoc, project_id: str, level: str) -> None:
    """Raises 403 if the user lacks the required access level on the project."""
    ok = can_view_project(user, project_id) if level == "view" else can_edit_project(user, project_id)
    if not ok:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"No {level} access to project {project_id}.",
        )


# Auth typed aliases
CurrentUserDep = Annotated[UserDoc | None, Depends(get_current_user)]
AuthUserDep = Annotated[UserDoc, Depends(require_user)]
AdminDep = Annotated[UserDoc, Depends(require_admin)]
