"""Negocio de `summary`: los 5 contadores del Home (pantalla p0).

GLOBAL (sin `projectId`) — totales de cada colección ACTIVA (`flgactive != False`):
  tables / views / relationships / subjectAreas (canvases) / projects.

POR PROYECTO (con `projectId`, doc 75 D6) — cada documento lleva su
`projectId`, así que los contadores son conteos directos por proyecto (una
tabla sin canvas también cuenta); `projects` = nº de proyectos seleccionados.
"""
from __future__ import annotations

from . import repository

_SCOPED = (("tables", "canonical_tables"), ("views", "views"),
           ("relationships", "relationships"), ("subjectAreas", "subject_areas"))


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


async def count_for_project(project_id: str) -> dict:
    """Contadores de UN proyecto: conteos directos por `projectId`."""
    out = {key: await repository.count_scoped(project_id, coll) for key, coll in _SCOPED}
    out["projects"] = 1
    return out


async def summary(project_ids: list[str] | None = None) -> dict:
    """Resuelve los 5 contadores del Home; global o acotado a uno o varios
    proyectos (suma de sus conteos — los proyectos son disjuntos, doc 75)."""
    ids = [pid for pid in dict.fromkeys(project_ids or []) if pid]  # dedup, orden
    if ids:
        total = {key: 0 for key, _ in _SCOPED}
        for pid in ids:
            counts = await count_for_project(pid)
            for key, _ in _SCOPED:
                total[key] += counts[key]
        total["projects"] = len(ids)
        return total

    return count_global(
        await repository.count_active("canonical_tables"),
        await repository.count_active("views"),
        await repository.count_active("relationships"),
        await repository.count_active("subject_areas"),
        await repository.count_active("projects"),
    )
