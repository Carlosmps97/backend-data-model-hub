"""Índices F1 (spec 10 §8/§9) declarados en `ensure_indexes` — db fake, sin BD real."""
from __future__ import annotations

import asyncio

from app.core.db.indexes import ensure_indexes


class _FakeColl:
    def __init__(self, name: str, calls: list):
        self._name = name
        self._calls = calls

    async def create_index(self, keys, **kwargs):
        self._calls.append((self._name, tuple(keys)))


class _FakeDb:
    def __init__(self):
        self.calls: list = []

    def __getitem__(self, name: str):
        return _FakeColl(name, self.calls)


def test_f1_indexes_declarados():
    db = _FakeDb()
    asyncio.run(ensure_indexes(db))
    # §9: chequeo de duplicados de tabla por (schema, physicalName). El campo
    # persistido es `schema` (alias del Pydantic `sql_schema`).
    assert ("canonical_tables", (("schema", 1), ("physicalName", 1))) in db.calls
    # §8: impact busca relaciones por columna de algún par (v2, doc 19).
    assert ("relationships", (("pairs.parentColumnId", 1),)) in db.calls
    assert ("relationships", (("pairs.childColumnId", 1),)) in db.calls
    # El canvas resuelve relaciones por extremos parent/child.
    assert ("relationships", (("parentTableId", 1),)) in db.calls
    assert ("relationships", (("childTableId", 1),)) in db.calls


# ── F5 — índices de `subject_areas` para reporting a nivel Modelo de Datos:
# wildcard de udpValues (seek en filtros UDP) + name (sort/keyset; a escala, un
# orden sin índice sería full-scan). Fake db que captura create_index (con
# kwargs) — sin BD real. Fakes propios para no chocar con los _Fake* de arriba
# (que capturan 2-tuplas sin kwargs).
class _FakeCollF5:
    def __init__(self, name: str, calls: list):
        self._name, self._calls = name, calls

    async def create_index(self, keys, **kwargs):
        self._calls.append((self._name, tuple(keys), kwargs))


class _FakeDbF5:
    def __init__(self):
        self.calls: list = []

    def __getitem__(self, name: str) -> _FakeCollF5:
        return _FakeCollF5(name, self.calls)


def test_subject_areas_indices_f5():
    db = _FakeDbF5()
    asyncio.run(ensure_indexes(db))
    assert ("subject_areas", (("udpValues.$**", 1),), {}) in db.calls
    assert ("subject_areas", (("name", 1),), {}) in db.calls
