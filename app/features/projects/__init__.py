"""API pública de la feature `projects`.

Modelos + funciones de repositorio (las usa `canvas`: `get_project`). El
`router` se importa desde `app.features.projects.router` en el composition root.
"""

from app.features.projects.models import DomainDoc, ModelLevelDoc, ProjectDoc
from app.features.projects.repository import (
    create_project,
    delete_project,
    generate_project_id,
    get_project,
    get_projects,
    update_project,
)

__all__ = [
    # models
    "DomainDoc",
    "ModelLevelDoc",
    "ProjectDoc",
    # repository
    "create_project",
    "delete_project",
    "generate_project_id",
    "get_project",
    "get_projects",
    "update_project",
]
