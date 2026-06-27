"""Feature `summary`: contadores de solo lectura para el Home (pantalla p0).

Expone `GET /api/summary?projectId=` con los 5 cards del Home
(Tables · Views · Relationships · Subject Areas · Projects), en alcance global
o acotado a un proyecto. Agregación pura sobre colecciones activas; no muta
nada. La API pública es `router`.
"""

from .router import router

__all__ = ["router"]
