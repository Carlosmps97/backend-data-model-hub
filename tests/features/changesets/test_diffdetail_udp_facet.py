"""Doc 69: el detalle de cambios usa el nombre por faceta (homónimos legibles)."""
from __future__ import annotations

from app.core.facets import udp_display_names
from app.features.changesets.diffdetail import _udp_rows

DEFS = [{"id": "u-p", "name": "Universal", "level": "table"},
        {"id": "u-l", "name": "Universal", "level": "table", "view": "logical"}]


def test_udp_rows_distingue_facetas():
    rows = _udp_rows({"udpValues": {"u-p": "Si", "u-l": "Si"}}, {"udpValues": {"u-p": "No", "u-l": "Si"}},
                     "modified", {"udp": udp_display_names(DEFS)})
    assert [r["label"] for r in rows] == ["UDP · Universal"]
    rows_all = _udp_rows({}, {"udpValues": {"u-p": "No", "u-l": "Si"}}, "created", {"udp": udp_display_names(DEFS)})
    assert sorted(r["label"] for r in rows_all) == ["UDP · Universal", "UDP · Universal (Logical)"]
