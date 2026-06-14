"""Routers de la API REST del backend de plataforma.

- `health`:        GET /api/health.
- `auth`:          POST /api/auth/login, POST /api/auth/logout, GET /api/auth/me.
- `admin`:         CRUD de usuarios + permisos.
- `projects`:      GET/POST /api/projects, GET/PUT/DELETE /api/projects/{id}
                   (el proyecto embebe engines/layers/domains).
- `canvas`:        GET/PUT /api/projects/{id}/canvas, PATCH /api/projects/{id}/positions.
- `excel_import`:  POST /api/excel-import/preview.

Las rutas de modelado conversacional (`/api/conversations/*`) viven
ahora en el servicio `app-agents-modeler`.
"""

from api.routes.admin import router as admin_router
from api.routes.auth import router as auth_router
from api.routes.canvas import router as canvas_router
from api.routes.excel_import import router as excel_import_router
from api.routes.health import router as health_router
from api.routes.projects import router as projects_router

__all__ = [
    "admin_router",
    "auth_router",
    "canvas_router",
    "excel_import_router",
    "health_router",
    "projects_router",
]
