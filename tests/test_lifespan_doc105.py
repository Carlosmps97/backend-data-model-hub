"""Doc 105 (H2): el arranque REAL de la app (lifespan) conecta y lanza en
segundo plano la purga de cambios huérfanos. Los demás tests corren sin
lifespan, así que un error acá (p. ej. un decorador mal puesto que hacía
fallar el arranque en cada reintento) no lo veía nadie."""
from __future__ import annotations

import time

from starlette.testclient import TestClient

from app.core.db import client as db_client
from app.main import app
from tests.support.fakedb import FakeDb


def test_el_arranque_conecta_y_purga_los_huerfanos(monkeypatch):
    fake = FakeDb()
    fake.raw["changesets"].insert_one({"_id": "vivo", "title": "t", "owner": "ana", "projectId": "p",
                                       "status": "draft"})
    fake.raw["changeset_changes"].insert_many([
        {"_id": "vivo::canonical_tables::a", "csId": "vivo", "collection": "canonical_tables", "entityId": "a",
         "op": "delete"},
        {"_id": "muerto::canonical_tables::b", "csId": "muerto", "collection": "canonical_tables",
         "entityId": "b", "op": "delete"},
    ])
    # la eliminación de «muerto» se cortó entre la cabecera y sus cambios: quedó su lápida
    fake.raw["deleted_changesets"].insert_one({"_id": "muerto", "at": 0})

    async def connect() -> None:
        monkeypatch.setattr(db_client, "_pg_db", fake)

    async def disconnect() -> None:
        return None

    monkeypatch.setattr(db_client, "connect", connect)
    monkeypatch.setattr(db_client, "disconnect", disconnect)
    with TestClient(app):
        assert app.state.db_connected is True           # antes: «db connection failed» en cada arranque
        for _ in range(100):
            if not fake.raw["changeset_changes"].count_documents({"csId": "muerto"}):
                break
            time.sleep(0.02)
        assert fake.raw["changeset_changes"].count_documents({"csId": "muerto"}) == 0
        assert fake.raw["changeset_changes"].count_documents({"csId": "vivo"}) == 1
