"""Dependencias compartidas de auth/permisos (capa FastAPI).

Patrón:
- `get_current_user` lee la cookie de sesión, verifica el JWT y recarga el
  `UserDoc` de Cosmos para que cualquier cambio de permisos se refleje al
  instante.
- `require_user` / `require_admin` / `require_editor` levantan 401/403.
- `project_access_level` espeja `web-data-model-hub/src/lib/auth/permissions.ts`.

Estas dependencias son la API pública de la feature `auth`: las usan los routers
de TODAS las demás features (`from app.features.auth import AuthUserDep, ...`).
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import Depends, HTTPException, Request, status

from app.core.security.jwt import AUTH_COOKIE_NAME, verify_token
from app.features.users import UserDoc, get_user_by_id


# ─── Helpers de permisos ─────────────────────────────────────────────────
# Espeja web-data-model-hub/src/lib/auth/permissions.ts


def project_access_level(user: UserDoc, project_id: str) -> Literal["view", "edit"] | None:
    """Devuelve el mejor nivel de acceso del usuario sobre un proyecto, o None."""
    if user.role == "admin":
        return "edit"
    best: Literal["view", "edit"] | None = None
    for perm in user.permissions:
        if perm.projectId == project_id:
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


def visible_project_ids(user: UserDoc) -> set[str]:
    return {p.projectId for p in user.permissions}


# ─── Auth dependencies ───────────────────────────────────────────────────


async def get_current_user(request: Request) -> UserDoc | None:
    """Lee la cookie de sesión, verifica el JWT y recarga el usuario de la DB.

    Siempre re-consulta la DB para que cambios de permiso/estado a mitad de
    sesión tengan efecto inmediato (mismo contrato que la capa TypeScript)."""
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
    """401 si la request no tiene sesión válida o el usuario está inactivo."""
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
    """403 si el usuario autenticado no es admin."""
    if not is_admin(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required.",
        )
    return user


async def require_editor(
    user: Annotated[UserDoc, Depends(require_user)],
) -> UserDoc:
    """403 si el usuario es viewer. Lo usa el catálogo transversal UDP /
    Semantic Type, donde cualquier no-viewer (admin o editor) puede escribir."""
    if user.role not in ("admin", "editor"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Editor or admin role required.",
        )
    return user


async def check_project_access(user: UserDoc, project_id: str, level: str) -> None:
    """403 si el usuario no tiene el nivel de acceso requerido sobre el proyecto."""
    ok_access = can_view_project(user, project_id) if level == "view" else can_edit_project(user, project_id)
    if not ok_access:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"No {level} access to project {project_id}.",
        )


# Aliases tipados de auth
CurrentUserDep = Annotated[UserDoc | None, Depends(get_current_user)]
AuthUserDep = Annotated[UserDoc, Depends(require_user)]
AdminDep = Annotated[UserDoc, Depends(require_admin)]
EditorDep = Annotated[UserDoc, Depends(require_editor)]
