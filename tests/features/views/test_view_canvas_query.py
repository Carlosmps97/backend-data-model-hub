"""Query del diagrama (F3a): vistas con showOnCanvas=True y ≥1 fuente en el
canvas. Matchea SOLO `sourceTableIds` (indexado, sin fallback legacy):
`showOnCanvas` nace en F3a, así que todo doc con True pasó por el write path
nuevo, que normaliza las fuentes."""
from __future__ import annotations

from app.features.views.repository import build_canvas_query


def test_canvas_query_shape():
    assert build_canvas_query(["t1", "t2"]) == {
        "flgactive": {"$ne": False},
        "showOnCanvas": True,
        "sourceTableIds": {"$in": ["t1", "t2"]},
    }
