"""DTOs de `summary`. Forma de respuesta de los 5 cards del Home (p0)."""
from __future__ import annotations

from pydantic import BaseModel


class SummaryCounts(BaseModel):
    """Los 5 contadores del Home. Espeja las 5 cards de la pantalla p0."""

    tables: int = 0
    views: int = 0
    relationships: int = 0
    subjectAreas: int = 0
    projects: int = 0
