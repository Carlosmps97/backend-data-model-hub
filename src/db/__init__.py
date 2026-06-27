"""Database layer for the platform backend (backend-data-model-hub).

Modules:
  motor_client  — async Motor (Cosmos DB) connection singleton.
  db_models     — Pydantic v2 document models for every collection.
  projects_db   — async CRUD for the `projects` collection (embeds engines/
                  layers/domains).
  canvas_db     — async CRUD for a project's canvas (`project_tables` +
                  `project_relationships`).
  users_db      — async CRUD for the `users` collection.

The agent's `column_catalog` lives in `app-agents-modeler/src/db/catalog_db.py`.
"""

from src.db import motor_client
from src.db.db_models import (
    DomainDoc,
    ModelLevelDoc,
    PermissionDoc,
    ProjectDoc,
    RelationshipDoc,
    TableColumnDoc,
    TableDoc,
    UserDoc,
    ViewDoc,
)
from src.db.canvas_db import (
    bulk_update_positions,
    delete_canvas,
    get_canvas,
    replace_canvas,
)
from src.db.projects_db import (
    create_project,
    delete_project,
    get_project,
    get_projects,
    update_project,
)
from src.db.users_db import (
    create_user,
    delete_user,
    get_user_by_id,
    get_user_by_username,
    list_users,
    mark_login_success,
    update_user,
)

__all__ = [
    # connection
    "motor_client",
    # document models
    "DomainDoc",
    "ModelLevelDoc",
    "PermissionDoc",
    "ProjectDoc",
    "RelationshipDoc",
    "TableColumnDoc",
    "TableDoc",
    "UserDoc",
    "ViewDoc",
    # projects
    "create_project",
    "delete_project",
    "get_project",
    "get_projects",
    "update_project",
    # canvas
    "bulk_update_positions",
    "delete_canvas",
    "get_canvas",
    "replace_canvas",
    # users
    "create_user",
    "delete_user",
    "get_user_by_id",
    "get_user_by_username",
    "list_users",
    "mark_login_success",
    "update_user",
]
