"""Routers de la API REST del Data Modeler Agent.

- `health`: GET /api/health, GET /api/engines.
- `conversations`: POST /api/conversations, POST /api/conversations/{id}/guidelines,
  DELETE /api/conversations/{id}.
- `modeling`: POST /api/conversations/{id}/model, GET /api/conversations/{id}/model.
- `auth`: POST /api/auth/login, POST /api/auth/logout, GET /api/auth/me.
- `projects`: GET/POST /api/projects, GET/PUT/DELETE /api/projects/{id}.
- `models`: GET/POST /api/models, GET/PUT/DELETE /api/models/{id}.
"""

from api.routes.admin import router as admin_router
from api.routes.auth import router as auth_router
from api.routes.conversations import router as conversations_router
from api.routes.health import router as health_router
from api.routes.modeling import router as modeling_router
from api.routes.models import router as models_router
from api.routes.projects import router as projects_router

__all__ = [
    "admin_router",
    "auth_router",
    "conversations_router",
    "health_router",
    "modeling_router",
    "models_router",
    "projects_router",
]
