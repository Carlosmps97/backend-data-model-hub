"""Negocio de `summary`. La agregación (`count_for_project`) es PURA y testeable
sin base de datos: recibe listas de docs ya leídas por el repository.

Semántica de los 5 contadores del Home (pantalla p0):

GLOBAL (sin `projectId`) — totales de cada colección ACTIVA (`flgactive != False`):
  - tables        = nº de `canonical_tables`
  - views         = nº de `views`
  - relationships = nº de `relationships`
  - subjectAreas  = nº de `subject_areas` (canvases)
  - projects      = nº de `projects`

POR PROYECTO (con `projectId`) — el proyecto es un contenedor cuyo contenido se
deriva de los canvases (subject_areas) que lo componen. Como `canonical_tables`
es un pool UNIVERSAL (no lleva `projectId`), el alcance de un proyecto se define
por las tablas que sus canvases referencian (`subject_areas.tableIds`):
  - tables        = nº de tableIds DISTINTOS referenciados por los canvases del
                    proyecto (una tabla compartida por dos canvases cuenta 1).
  - views         = nº de vistas cuyo `tableId` está entre esas tablas. Las
                    vistas sin `tableId` (no atadas a una tabla) NO cuentan en el
                    alcance de un proyecto.
  - relationships = nº de relaciones cuyas DOS puntas (source y target) están
                    entre esas tablas (relación interna al proyecto).
  - subjectAreas  = nº de canvases del proyecto.
  - projects      = 1 (el propio proyecto).
"""
from __future__ import annotations

from . import repository


def _project_table_ids(subject_areas: list[dict]) -> set[str]:
    """Tablas (DISTINTAS) referenciadas por los canvases dados. Puro."""
    ids: set[str] = set()
    for sa in subject_areas:
        for tid in sa.get("tableIds") or []:
            ids.add(tid)
    return ids


def count_for_project(
    subject_areas: list[dict],
    views: list[dict],
    relationships: list[dict],
) -> dict:
    """Contadores acotados a un proyecto a partir de SUS canvases. Puro.

    `subject_areas` = canvases del proyecto; `views`/`relationships` = pools
    globales (se filtran al alcance del proyecto aquí). Ver semántica en el
    docstring del módulo.
    """
    table_ids = _project_table_ids(subject_areas)
    view_count = sum(1 for v in views if (v.get("tableId") in table_ids))
    rel_count = sum(
        1
        for r in relationships
        if r.get("parentTableId") in table_ids and r.get("childTableId") in table_ids
    )
    return {
        "tables": len(table_ids),
        "views": view_count,
        "relationships": rel_count,
        "subjectAreas": len(subject_areas),
        "projects": 1,
    }


def count_global(
    tables_total: int,
    views_total: int,
    relationships_total: int,
    subject_areas_total: int,
    projects_total: int,
) -> dict:
    """Contadores globales (totales de cada colección activa). Puro."""
    return {
        "tables": tables_total,
        "views": views_total,
        "relationships": relationships_total,
        "subjectAreas": subject_areas_total,
        "projects": projects_total,
    }


async def summary(project_ids: list[str] | None = None) -> dict:
    """Resuelve los 5 contadores del Home; global o acotado a uno o varios
    proyectos (unión deduplicada de su alcance)."""
    ids = [pid for pid in dict.fromkeys(project_ids or []) if pid]  # dedup, orden
    if ids:
        subject_areas: list[dict] = []
        for pid in ids:
            subject_areas.extend(await repository.subject_areas_for_project(pid))
        views = await repository.views_with_table()
        relationships = await repository.relationships_min()
        counts = count_for_project(subject_areas, views, relationships)
        counts["projects"] = len(ids)  # nº de proyectos seleccionados (no 1)
        return counts

    return count_global(
        await repository.count_active("canonical_tables"),
        await repository.count_active("views"),
        await repository.count_active("relationships"),
        await repository.count_active("subject_areas"),
        await repository.count_active("projects"),
    )
