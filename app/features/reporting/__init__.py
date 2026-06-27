"""Feature `reporting`: agregación tabular de solo lectura (pantalla pr).

Dos lecturas:
  - `GET /api/reporting/tables`  → una fila por tabla canónica (schema, subject
    areas, projects, #columnas, #relaciones, def funcional), con filtros
    opcionales (`schema`, `projectId`, …).
  - `GET /api/reporting/columns` → detalle a nivel columna para el export por
    niveles ("Export to Excel"); acotable por `tableId`.

No muta nada. La agregación (`table_rows`, `column_rows`) es PURA y testeable.
API pública: `router`.
"""

from .router import router

__all__ = ["router"]
