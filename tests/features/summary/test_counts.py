"""Agregación pura de los contadores del Home (`summary.service`)."""
from __future__ import annotations

from app.features.summary.service import count_for_project, count_global


def test_count_global_passthrough():
    assert count_global(10, 4, 7, 3, 2) == {
        "tables": 10,
        "views": 4,
        "relationships": 7,
        "subjectAreas": 3,
        "projects": 2,
    }


def test_count_for_project_distinct_tables_across_canvases():
    # t1 aparece en dos canvases → cuenta 1 (DISTINTAS).
    subject_areas = [
        {"tableIds": ["t1", "t2"]},
        {"tableIds": ["t1", "t3"]},
    ]
    out = count_for_project(subject_areas, views=[], relationships=[])
    assert out["tables"] == 3  # {t1, t2, t3}
    assert out["subjectAreas"] == 2
    assert out["projects"] == 1


def test_count_for_project_views_only_within_scope():
    subject_areas = [{"tableIds": ["t1", "t2"]}]
    views = [
        {"tableId": "t1"},   # dentro
        {"tableId": "t2"},   # dentro
        {"tableId": "t9"},   # fuera del proyecto
        {"tableId": None},   # vista sin tabla → no cuenta
    ]
    out = count_for_project(subject_areas, views=views, relationships=[])
    assert out["views"] == 2


def test_count_for_project_relationships_need_both_ends_inside():
    subject_areas = [{"tableIds": ["t1", "t2", "t3"]}]
    relationships = [
        {"parentTableId": "t1", "childTableId": "t2"},  # ambas dentro → cuenta
        {"parentTableId": "t1", "childTableId": "t9"},  # una fuera → no cuenta
        {"parentTableId": "t8", "childTableId": "t9"},  # ambas fuera → no cuenta
        {"parentTableId": "t2", "childTableId": "t3"},  # ambas dentro → cuenta
    ]
    out = count_for_project(subject_areas, views=[], relationships=relationships)
    assert out["relationships"] == 2


def test_count_for_project_empty():
    out = count_for_project([], views=[{"tableId": "t1"}], relationships=[])
    assert out == {
        "tables": 0,
        "views": 0,
        "relationships": 0,
        "subjectAreas": 0,
        "projects": 1,
    }
