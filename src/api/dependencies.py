"""Dependencias compartidas de la capa FastAPI del backend de plataforma.

Solo wiring de autenticación y permisos. El backend NO hospeda al agente
de modelado: los providers de `ConversationStore`, `FoundryChatClient` y
el semáforo de pipeline viven ahora en `app-agents-modeler`.

Patrón:
- `get_current_user` lee la cookie de sesión, verifica el JWT y recarga
  el `UserDoc` de Cosmos para que cualquier cambio de permisos se
  refleje al instante.
- `require_user` / `require_admin` levantan 401/403 cuando aplica.
- Los helpers `project_access_level` / `model_access_level` reflejan
  el contrato de `web-data-model-hub/src/lib/auth/permissions.ts`.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request, status

from src.api.auth import AUTH_COOKIE_NAME, verify_token
from src.db.db_models import UserDoc
from src.db.users_db import get_user_by_id


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
