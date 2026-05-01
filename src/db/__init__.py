"""Database layer for the Data Modeler Agent backend.

Modules:
  motor_client  — async Motor (Cosmos DB) connection singleton.
  db_models     — Pydantic v2 document models for every collection.
  projects_db   — async CRUD for the `projects` collection.
  models_db     — async CRUD for models + child collections.
  users_db      — async CRUD for the `users` collection.
  catalog_db    — sync pymongo CRUD for `column_catalog` (used by agent tools).
"""

from src.db import motor_client
from src.db.db_models import (
    DataModelDoc,
    DomainDefinitionDoc,
    PermissionDoc,
    ProjectDoc,
    RelationshipDoc,
    SubdomainDefinitionDoc,
    TableColumnDoc,
    TableModelDoc,
    UserDoc,
    ViewModelDoc,
)
from src.db.models_db import (
    create_model,
    delete_model,
    get_model,
    get_models,
    update_model,
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
    # models
    "DataModelDoc",
    "DomainDefinitionDoc",
    "PermissionDoc",
    "ProjectDoc",
    "RelationshipDoc",
    "SubdomainDefinitionDoc",
    "TableColumnDoc",
    "TableModelDoc",
    "UserDoc",
    "ViewModelDoc",
    # projects
    "create_project",
    "delete_project",
    "get_project",
    "get_projects",
    "update_project",
    # models
    "create_model",
    "delete_model",
    "get_model",
    "get_models",
    "update_model",
    # users
    "create_user",
    "delete_user",
    "get_user_by_id",
    "get_user_by_username",
    "list_users",
    "mark_login_success",
    "update_user",
]
