"""Doc 94 D12 · C11 de `audit_data_consistency`: `$unset` idempotente de los
campos retirados — `glossary_terms.wordType` y `canonical_columns.pkPosition`
(final review #5). No es un backfill: es un chequeo numerado más del saneo
estructural."""
from __future__ import annotations

from scripts import audit_data_consistency as audit


class _Coll:
    def __init__(self, field, docs):
        self.field = field
        self.docs = docs
        self.calls = []

    def count_documents(self, flt):
        assert flt == {self.field: {"$exists": True}}
        return sum(1 for d in self.docs if self.field in d)

    def update_many(self, flt, upd):
        self.calls.append((flt, upd))
        assert flt == {self.field: {"$exists": True}} and upd == {"$unset": {self.field: ""}}
        for d in self.docs:
            d.pop(self.field, None)


class _Db:
    def __init__(self, terms, columns):
        self.glossary_terms = _Coll("wordType", terms)
        self.canonical_columns = _Coll("pkPosition", columns)

    def __getitem__(self, name):
        return getattr(self, name)


def test_c11_reporta_sin_fix_y_limpia_con_fix_idempotente():
    db = _Db([{"_id": "a", "wordType": "prime"}, {"_id": "b"}, {"_id": "c", "wordType": None}],
             [{"_id": "k", "pkPosition": 0}, {"_id": "x"}])
    assert audit.unset_retired_fields(db, fix=False) == {"glossary_terms.wordType": 2, "canonical_columns.pkPosition": 1}
    assert db.glossary_terms.calls == [] and db.canonical_columns.calls == []
    assert audit.unset_retired_fields(db, fix=True) == {"glossary_terms.wordType": 2, "canonical_columns.pkPosition": 1}
    assert all("wordType" not in d for d in db.glossary_terms.docs)
    assert all("pkPosition" not in d for d in db.canonical_columns.docs)
    assert audit.unset_retired_fields(db, fix=True) == {"glossary_terms.wordType": 0, "canonical_columns.pkPosition": 0}
    assert len(db.glossary_terms.calls) == 1 and len(db.canonical_columns.calls) == 1
