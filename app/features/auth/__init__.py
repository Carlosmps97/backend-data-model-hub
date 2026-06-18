"""API pública de la feature `auth`.

Expone las dependencias de auth/permisos que usan los routers de las demás
features. El `router` se importa directo desde `app.features.auth.router` en el
composition root (`app/main.py`) — no se reexporta acá para evitar ciclos con
`users`.
"""

from app.features.auth.dependencies import (
    AdminDep,
    AuthUserDep,
    CurrentUserDep,
    EditorDep,
    can_edit_project,
    can_view_project,
    check_project_access,
    get_current_user,
    is_admin,
    project_access_level,
    require_admin,
    require_editor,
    require_user,
    visible_project_ids,
)

__all__ = [
    "AdminDep",
    "AuthUserDep",
    "CurrentUserDep",
    "EditorDep",
    "can_edit_project",
    "can_view_project",
    "check_project_access",
    "get_current_user",
    "is_admin",
    "project_access_level",
    "require_admin",
    "require_editor",
    "require_user",
    "visible_project_ids",
]
