"""Membresía de vistas en un canvas (doc 70 §2) — funciones PURAS, sin DB.

Hasta el doc 70 una vista era visible en TODO canvas que contuviera alguna de
sus fuentes (`showOnCanvas` global, doc 10 D3). Desde el doc 70 el canvas
declara sus miembros en `subject_areas.viewIds`, igual que `tableIds`:

- `viewIds is None`  → canvas LEGACY (nunca materializó su lista): rige la
  regla vieja `showOnCanvas ∧ fuentes ∩ tableIds`.
- `viewIds = [...]`  → sólo esas vistas, y sólo si conservan ≥1 fuente en el
  canvas (defensivo: una vista cuyas fuentes salieron todas no se dibuja).

Este módulo es la ÚNICA definición de esa regla: la consumen el diagrama
(`projects/service.diagram`), el listado por tabla (`views/service.table_views`),
el reporting (`canvases` de cada vista) y el backfill.
"""
from __future__ import annotations


def view_sources(view: dict) -> list[str]:
    """Fuentes canónicas de la vista con fallback al `tableId` legacy. Pura."""
    src = view.get("sourceTableIds") or []
    if src:
        return list(src)
    tid = view.get("tableId")
    return [tid] if tid else []


def filter_canvas_views(views: list[dict], view_ids: list[str] | None,
                        table_ids: list[str]) -> list[dict]:
    """Vistas visibles en un canvas según su membresía (ver docstring del
    módulo). Conserva el orden de `views`. Pura."""
    present = set(table_ids or [])
    if view_ids is None:
        return [v for v in views
                if v.get("showOnCanvas") and set(view_sources(v)) & present]
    wanted = set(view_ids)
    return [v for v in views
            if v.get("id") in wanted and set(view_sources(v)) & present]


def view_on_canvas(view: dict, sa: dict) -> bool:
    """¿La vista es miembro visible del canvas `sa`? Mismo criterio que
    `filter_canvas_views`, para UN par (vista, canvas). Pura."""
    return bool(filter_canvas_views([view], sa.get("viewIds"), sa.get("tableIds") or []))
