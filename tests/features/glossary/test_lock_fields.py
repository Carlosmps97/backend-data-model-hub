"""F2 #1: campos de lock en AbbreviationDoc + round-trip en el snapshot."""
from __future__ import annotations

from app.features.data_standards.service import snapshot_of
from app.features.glossary.models import AbbreviationDoc


def test_doc_lock_defaults():
    d = AbbreviationDoc.model_validate({"projectId": "p1", "term": "codigo", "abbrev": "COD"})
    dumped = d.model_dump()
    assert dumped["locked"] is False
    assert dumped["lockedBy"] is None
    assert dumped["lockedAt"] is None


def test_doc_persiste_lock():
    d = AbbreviationDoc.model_validate({"projectId": "p1", 
        "term": "cuenta", "abbrev": "CTA", "locked": True,
        "lockedBy": "admin", "lockedAt": "2026-07-10T00:00:00+00:00",
    })
    dumped = d.model_dump()
    assert dumped["locked"] is True
    assert dumped["lockedBy"] == "admin"
    assert dumped["lockedAt"] == "2026-07-10T00:00:00+00:00"


def test_snapshot_of_incluye_lock():
    snap = snapshot_of(
        domains=[],
        terms=[{"id": "t1", "term": "codigo", "abbrev": "COD", "scope": "column",
                "wordType": "prime", "locked": True, "lockedBy": "admin",
                "lockedAt": "2026-07-10T00:00:00+00:00"}],
        naming={"column": {"separator": "_", "case": "upper"},
                "table": {"separator": "", "case": "upper"}},
    )
    assert snap["dict"][0]["locked"] is True
    assert snap["dict"][0]["lockedBy"] == "admin"
    assert snap["dict"][0]["lockedAt"] == "2026-07-10T00:00:00+00:00"
