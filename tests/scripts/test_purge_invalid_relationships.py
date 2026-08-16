"""Clasificación pura del purge N=N (doc 47) — sin BD."""
from __future__ import annotations

from scripts.purge_invalid_relationships import classify


def test_clasifica_solo_las_que_no_migran_la_llave_completa():
    rels = [
        {"id": "ok", "parentTableId": "P", "childTableId": "H",
         "pairs": [{"parentColumnId": "a", "childColumnId": "x"},
                   {"parentColumnId": "b", "childColumnId": "y"}]},
        {"id": "corta", "parentTableId": "P", "childTableId": "H",
         "pairs": [{"parentColumnId": "a", "childColumnId": "x"}]},
        {"id": "padre-sin-llave", "parentTableId": "Q", "childTableId": "H",
         "pairs": [{"parentColumnId": "q1", "childColumnId": "z"}]},
    ]
    bad = classify(rels, {"P": {"a", "b"}, "Q": set()})
    assert [r["id"] for r in bad] == ["corta", "padre-sin-llave"]
    assert bad[0]["expected"] == 2 and bad[0]["got"] == 1
