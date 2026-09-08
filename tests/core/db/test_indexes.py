"""`ensure_indexes` sobre una DB fake que registra las llamadas — no toca el
store real. Doc 73 §11.2: los campos ARRAY (`views.sourceTableIds`,
`subject_areas.viewIds`) ya NO llevan btree por campo (los sirve el GIN de la
tabla y el btree reventaba con arrays grandes); los legacy se retiran."""
from __future__ import annotations

import asyncio

from app.core.db.indexes import ensure_indexes


class _FakeColl:
    def __init__(self, name: str, calls: list):
        self._name, self._calls = name, calls

    async def create_index(self, keys, **kwargs):
        self._calls.append((self._name, tuple(keys)))

    async def drop_index(self, idx_name):
        self._calls.append((self._name, "DROP", idx_name))


class _FakeDb:
    def __init__(self):
        self.calls: list = []

    def __getitem__(self, name: str) -> _FakeColl:
        return _FakeColl(name, self.calls)


def test_array_fields_sin_btree_y_legacy_retirados():
    db = _FakeDb()
    asyncio.run(ensure_indexes(db))
    # Arrays: sin btree por campo (el GIN de la tabla cubre el array-contains).
    assert ("views", (("sourceTableIds", 1),)) not in db.calls
    assert ("subject_areas", (("viewIds", 1),)) not in db.calls
    # Los btree legacy se retiran (BDs creadas con el doc 70).
    assert ("subject_areas", "DROP", "ix_subject_areas_viewIds") in db.calls
    assert ("views", "DROP", "ix_views_sourceTableIds") in db.calls
    # Los escalares siguen: tableId legacy (fallback OR de docs no migrados) y flgactive.
    assert ("views", (("tableId", 1),)) in db.calls
    assert ("views", (("flgactive", 1),)) in db.calls
    assert ("subject_areas", (("projectId", 1),)) in db.calls


def test_indices_por_proyecto_doc75():
    db = _FakeDb()
    asyncio.run(ensure_indexes(db))
    assert ("canonical_tables", (("projectId", 1), ("physicalName", 1))) in db.calls
    assert ("canonical_columns", (("projectId", 1), ("physicalName", 1))) in db.calls
    assert ("schemas", (("projectId", 1), ("name", 1))) in db.calls
    for coll in ("relationships", "views", "parent_domains", "glossary_terms", "udp_definitions",
                 "naming_config", "ddl_rules", "folders", "subject_areas", "saved_reports"):
        assert (coll, (("projectId", 1),)) in db.calls, coll
    assert ("changesets", (("projectId", 1), ("status", 1))) in db.calls
    assert ("changesets", (("projectId", 1), ("appliedAt", 1))) in db.calls
    assert ("standards_versions", (("projectId", 1), ("seq", 1))) in db.calls
    for coll, idx in (("standards_versions", "ix_standards_versions_seq"),
                      ("canonical_tables", "ix_canonical_tables_schema_physicalName"),
                      ("folders", "ix_folders_projectId"), ("subject_areas", "ix_subject_areas_projectId")):
        assert (coll, "DROP", idx) in db.calls, idx
    assert ("canonical_tables", (("schema", 1), ("physicalName", 1))) not in db.calls
    assert ("standards_versions", (("seq", 1),)) not in db.calls
