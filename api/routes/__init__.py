"""Routers de la API REST del Data Modeler Agent.

- `health`: GET /api/health, GET /api/engines.
- `conversations`: POST /api/conversations, POST /api/conversations/{id}/guidelines,
  DELETE /api/conversations/{id}.
- `modeling`: POST /api/conversations/{id}/model, GET /api/conversations/{id}/model.
"""

from api.routes.conversations import router as conversations_router
from api.routes.health import router as health_router
from api.routes.modeling import router as modeling_router

__all__ = ["health_router", "conversations_router", "modeling_router"]
