"""Routers de la API REST del backend de plataforma.

- `health`:   GET /api/health.
- `auth`:     POST /api/auth/login, POST /api/auth/logout, GET /api/auth/me.
- `admin`:    CRUD de usuarios + permisos.
- `projects`: GET/POST /api/projects, GET/PUT/DELETE /api/projects/{id}.
- `models`:   GET/POST /api/models, GET/PUT/DELETE /api/models/{id}.

Las rutas de modelado conversacional (`/api/conversations/*`) viven
ahora en el servicio `app-agents-modeler`.
"""

from api.routes.admin import router as admin_router
from api.routes.auth import router as auth_router
from api.routes.health import router as health_router
from api.routes.models import router as models_router
from api.routes.projects import router as projects_router

__all__ = [
    "admin_router",
    "auth_router",
    "health_router",
    "models_router",
    "projects_router",
]
