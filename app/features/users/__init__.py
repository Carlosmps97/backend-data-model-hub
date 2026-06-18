"""API pública de la feature `users`.

Expone modelos + funciones de repositorio (las usa `auth`). El `router` se
importa directo desde `app.features.users.router` en el composition root
(`app/main.py`) — no acá, para evitar ciclos con `auth`.
"""

from app.features.users.models import PermissionDoc, UserDoc
from app.features.users.repository import (
    create_user,
    delete_user,
    get_user_by_id,
    get_user_by_username,
    list_users,
    mark_login_success,
    update_user,
)

__all__ = [
    # models
    "PermissionDoc",
    "UserDoc",
    # repository
    "create_user",
    "delete_user",
    "get_user_by_id",
    "get_user_by_username",
    "list_users",
    "mark_login_success",
    "update_user",
]
