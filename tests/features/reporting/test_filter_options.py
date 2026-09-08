"""Doc 70 §4.1: universo completo de los filtros del reporte tabular — por
PROYECTO (doc 75): esquemas y canvases del proyecto; ya no se listan
proyectos (el reporte es de uno solo)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.reporting import service
from app.features.reporting.service import filter_option_lists


def test_filter_option_lists_limpia_deduplica_y_ordena_ci():
    out = filter_option_lists(
        schemas=["core", None, "", "Risk", "core", " analytics "],
        subject_areas=["B", "a", "B"],
    )
    assert out == {"schemas": ["analytics", "core", "Risk"],
                   "subjectAreas": ["a", "B"]}


def test_filter_options_orquesta_el_repository_por_proyecto(monkeypatch):
    repo = AsyncMock(return_value={"schemas": ["s2", "s1"], "subjectAreas": ["C2", "C1"]})
    monkeypatch.setattr(service.repository, "filter_options", repo)
    out = asyncio.run(service.filter_options("p1"))
    assert out == {"schemas": ["s1", "s2"], "subjectAreas": ["C1", "C2"]}
    repo.assert_awaited_once_with("p1")
