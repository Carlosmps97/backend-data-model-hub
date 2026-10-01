"""Doc 105 (revisión R2, hallazgo 5) · `cleanup()` del E2E en vivo: además de
las colecciones de alcance y del ledger, borra los jobs de la carga Excel
(`upload_jobs`) y sus cuerpos (`upload_job_bodies`) de los changesets de los
proyectos que creó. No llevan `projectId` (cuelgan del changeset por `csId`; el
cuerpo usa el id del job) y quedaban en el backend vivo tras cada corrida.

`_cleanup_async` corre acá sobre la BD fake (conexión parchada); lo de OTRO
proyecto no se toca."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import bcrypt

from app.core.db import client as db_client
from app.main import app
from scripts.create_admin import build_roles
from scripts.e2e import harness as H
from scripts.e2e.scenarios import ALL
from tests.support.fakedb import FakeDb


def _patch_db(monkeypatch, db: FakeDb) -> None:
    async def _noop() -> None:
        return None

    async def _get_db() -> FakeDb:
        return db

    monkeypatch.setattr(db_client, "connect", _noop)
    monkeypatch.setattr(db_client, "disconnect", _noop)
    monkeypatch.setattr(db_client, "get_db", _get_db)


def _ids(db: FakeDb, coll: str) -> set[str]:
    return {d["_id"] for d in db.raw[coll].find({}, {"_id": 1})}


def test_cleanup_borra_los_jobs_de_carga_y_sus_cuerpos_de_los_proyectos_del_e2e(monkeypatch):
    db = FakeDb()
    raw = db.raw
    raw["projects"].insert_many([{"_id": "p-e2e"}, {"_id": "p-otro"}])
    raw["changesets"].insert_many([{"_id": "cs-e2e", "projectId": "p-e2e"},
                                   {"_id": "cs-e2e-2", "projectId": "p-e2e"},
                                   {"_id": "cs-otro", "projectId": "p-otro"}])
    raw["changeset_changes"].insert_many([{"_id": "x1", "csId": "cs-e2e"}, {"_id": "x2", "csId": "cs-otro"}])
    raw["canonical_tables"].insert_many([{"_id": "t1", "projectId": "p-e2e"}, {"_id": "t2", "projectId": "p-otro"}])
    raw["upload_jobs"].insert_many([
        {"_id": "j1", "csId": "cs-e2e", "owner": "carla", "status": "validated"},
        {"_id": "j2", "csId": "cs-e2e-2", "owner": "carla", "status": "applied"},
        {"_id": "j3", "csId": "cs-otro", "owner": "juan", "status": "validated"}])
    raw["upload_job_bodies"].insert_many([{"_id": "j1", "body": {}}, {"_id": "j3", "body": {}}])
    _patch_db(monkeypatch, db)
    monkeypatch.setattr(H, "_CREATED", {"projects": ["p-e2e"]})

    asyncio.run(H._cleanup_async())

    assert _ids(db, "upload_jobs") == {"j3"}
    assert _ids(db, "upload_job_bodies") == {"j3"}
    assert _ids(db, "changesets") == {"cs-otro"}
    assert _ids(db, "changeset_changes") == {"x2"}
    assert _ids(db, "canonical_tables") == {"t2"}
    assert _ids(db, "projects") == {"p-otro"}


def test_cleanup_tras_el_escenario_real_de_carga_no_deja_jobs(monkeypatch):
    """El escenario `s21_bulk_upload` (validar, aplicar, fallar, descartar) en
    memoria y después la limpieza sobre la MISMA BD: no queda ningún job ni
    cuerpo de sus changesets."""
    db = FakeDb()
    now = datetime.now(timezone.utc).isoformat()
    for role in build_roles():
        db.raw["roles"].insert_one(role)
    for key, (username, password) in H.ROLE_USER.items():
        db.raw["users"].insert_one({
            "_id": username, "email": f"{username}@local", "name": username.title(), "role": H.ROLE_MATRIX[key],
            "projectIds": [], "status": "active", "flgactive": True, "createdAt": now, "updatedAt": now,
            "passwordHash": bcrypt.hashpw(password.encode(), bcrypt.gensalt(4)).decode()})
    monkeypatch.setattr(db_client, "_pg_db", db)
    monkeypatch.setattr(app.state, "db_connected", True, raising=False)
    monkeypatch.setattr(H, "_HTTP_FACTORY", None)
    monkeypatch.setattr(H, "_CREATED", {})
    H.use_app(app)
    summary = ALL["s21_bulk_upload"]().summary()
    assert summary["failed"] == 0, [c for c in summary["checks"] if not c["ok"]]
    assert db.raw["upload_jobs"].count_documents({}) > 0                     # el escenario dejó jobs
    _patch_db(monkeypatch, db)

    asyncio.run(H._cleanup_async())

    assert db.raw["upload_jobs"].count_documents({}) == 0
    assert db.raw["upload_job_bodies"].count_documents({}) == 0
    assert db.raw["changesets"].count_documents({}) == 0
