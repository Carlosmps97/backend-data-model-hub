"""`tables_in_area` filtra el pool canónico a las tablas de la Subject Area."""
from __future__ import annotations

from app.features.projects.service import tables_in_area


def test_tables_in_area_filtra():
    pool = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    assert tables_in_area(pool, ["a", "c"]) == [{"id": "a"}, {"id": "c"}]
