"""Contadores del Home (`summary.service`): global puro + por proyecto (doc 75
D6: conteos directos por projectId, repositorio mockeado)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.summary import service
from app.features.summary.service import count_global


def test_count_global_passthrough():
    assert count_global(10, 4, 7, 3, 2) == {
        "tables": 10,
        "views": 4,
        "relationships": 7,
        "subjectAreas": 3,
        "projects": 2,
    }


def _scoped(monkeypatch, data: dict[tuple[str, str], int]):
    calls: list[tuple[str, str]] = []

    async def _count(project_id, collection):
        calls.append((project_id, collection))
        return data.get((project_id, collection), 0)
    monkeypatch.setattr(service.repository, "count_scoped", _count)
    return calls


def test_count_for_project_cuenta_por_project_id(monkeypatch):
    calls = _scoped(monkeypatch, {("p1", "canonical_tables"): 3, ("p1", "views"): 2,
                                  ("p1", "relationships"): 1, ("p1", "subject_areas"): 4})
    out = asyncio.run(service.count_for_project("p1"))
    assert out == {"tables": 3, "views": 2, "relationships": 1, "subjectAreas": 4, "projects": 1}
    assert {c[0] for c in calls} == {"p1"}


def test_summary_varios_proyectos_suma_y_cuenta_proyectos(monkeypatch):
    _scoped(monkeypatch, {("p1", "canonical_tables"): 3, ("p2", "canonical_tables"): 5,
                          ("p2", "views"): 1})
    out = asyncio.run(service.summary(["p1", "p2", "p1"]))       # dedup
    assert out == {"tables": 8, "views": 1, "relationships": 0, "subjectAreas": 0, "projects": 2}


def test_summary_global_usa_count_active(monkeypatch):
    monkeypatch.setattr(service.repository, "count_active", AsyncMock(side_effect=[10, 4, 7, 3, 2]))
    assert asyncio.run(service.summary(None)) == {"tables": 10, "views": 4, "relationships": 7,
                                                   "subjectAreas": 3, "projects": 2}
