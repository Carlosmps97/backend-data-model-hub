"""`GET /api/catalog/tables?schema=X` (modal "New table · Import existing").

El filtro por esquema debe llegar al backend: el repository lo pone en el filtro
Mongo (junto a `q`), el service lo reenvía y el router acepta el query param.
Antes el esquema se filtraba en el cliente sobre la página de 50 (incompleto)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.features.catalog import repository, service


class _FakeCursor:
    def __init__(self, docs):
        self._docs = docs

    def sort(self, *a, **k):
        return self

    def limit(self, *a, **k):
        return self

    async def to_list(self, n):
        return self._docs


class _FakeColl:
    def __init__(self, docs, seen):
        self._docs = docs
        self._seen = seen

    def find(self, flt, projection=None):
        self._seen.append(flt)
        return _FakeCursor(self._docs)


class _FakeDb:
    def __init__(self, by_coll, seen):
        self._by = by_coll
        self._seen = seen

    def __getitem__(self, name):
        return _FakeColl(self._by.get(name, []), self._seen)


def _patch_db(monkeypatch, by_coll):
    seen: list[dict] = []

    async def _get_db():
        return _FakeDb(by_coll, seen)

    monkeypatch.setattr(repository, "get_db", _get_db)
    return seen


CORE_T = {"_id": "t1", "physicalName": "CLIENTE", "logicalName": "cliente", "schema": "CORE"}


def test_repo_pone_schema_en_el_filtro(monkeypatch):
    """`schema` entra al filtro Mongo por igualdad (no client-side)."""
    seen = _patch_db(monkeypatch, {"canonical_tables": [CORE_T]})
    out = asyncio.run(repository.list_tables(q=None, limit=50, schema="CORE"))
    assert [t["id"] for t in out] == ["t1"]
    assert seen[0]["schema"] == "CORE"


def test_repo_combina_schema_y_q(monkeypatch):
    """Con `schema` + `q` el filtro lleva AMBOS (esquema por igualdad y nombre
    por regex `$or`) — buscar por nombre dentro del esquema."""
    seen = _patch_db(monkeypatch, {"canonical_tables": [CORE_T]})
    asyncio.run(repository.list_tables(q="clie", limit=50, schema="CORE"))
    assert seen[0]["schema"] == "CORE"
    assert "$or" in seen[0]  # regex de nombre físico/lógico


def test_repo_sin_schema_no_agrega_filtro(monkeypatch):
    """Sin `schema` el filtro no incluye la clave (contrato original)."""
    seen = _patch_db(monkeypatch, {"canonical_tables": [CORE_T]})
    asyncio.run(repository.list_tables(q=None, limit=50))
    assert "schema" not in seen[0]


def test_endpoint_tables_reenvia_schema_al_service(monkeypatch):
    """GET /api/catalog/tables?q=..&limit=..&schema=.. reenvía los 3 al service
    (antes `schema` no era query param y el filtro no llegaba al backend)."""
    from starlette.testclient import TestClient

    from app.main import app

    spy = AsyncMock(return_value=[CORE_T | {"id": "t1"}])
    monkeypatch.setattr(service, "list_tables", spy)

    client = TestClient(app, raise_server_exceptions=False)
    r = client.get("/api/catalog/tables?q=clie&limit=50&schema=CORE")
    assert r.status_code == 200
    assert r.json()["success"] is True
    spy.assert_awaited_once_with("clie", 50, "CORE")
