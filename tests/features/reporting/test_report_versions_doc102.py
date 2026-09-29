"""Doc 102: la versión del Reporting — producción (sin id) o una versión PROPIA
sin publicar del MISMO proyecto; 404 / 409 / 403 legibles, nunca producción en
silencio."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.core.db import client as db_client
from app.core.identity.models import Principal
from app.features.changesets import access
from app.features.reporting import versions
from tests.support.fakedb import FakeDb

ANA = Principal(email="ana@x.pe", username="ana", display_name="Ana", source="local")
LUIS = Principal(email="luis@x.pe", username="luis", display_name="Luis", source="local")


@pytest.fixture
def db(monkeypatch) -> FakeDb:
    fake = FakeDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    monkeypatch.setattr(access.auth_service, "resolve_session_user", AsyncMock(return_value=None))
    base = {"title": "t", "owner": "ana", "projectId": "p1"}
    fake.raw["changesets"].insert_many([
        {"_id": "cs-draft", **base, "status": "draft"},
        {"_id": "cs-review", **base, "status": "submitted", "reviewers": ["rev"]},
        {"_id": "cs-pub", **base, "status": "approved", "appliedAt": "2026-09-01T00:00:00+00:00"},
        {"_id": "cs-closed", **base, "status": "rejected"},
        {"_id": "cs-otro", **{**base, "projectId": "p2"}, "status": "draft"},
    ])
    fake.raw["changeset_changes"].insert_many([
        {"_id": "cs-draft::canonical_tables::t1", "csId": "cs-draft", "collection": "canonical_tables",
         "entityId": "t1", "op": "upsert", "payload": {"projectId": "p1", "physicalName": "A"}, "at": "x"},
        {"_id": "cs-draft::views::v1", "csId": "cs-draft", "collection": "views",
         "entityId": "v1", "op": "delete", "at": "x"},
    ])
    return fake


def _run(cs_id, principal=ANA, collections=("canonical_tables",)):
    return asyncio.run(versions.version_changes("p1", cs_id, principal, list(collections)))


def test_sin_version_es_produccion(db):
    assert _run(None) is None and _run("") is None


def test_version_propia_devuelve_solo_las_colecciones_pedidas(db):
    out = _run("cs-draft")
    assert set(out) == {"canonical_tables"} and out["canonical_tables"]["t1"]["op"] == "upsert"
    assert _run("cs-review") == {}                          # en revisión, sin cambios de esa colección


@pytest.mark.parametrize("cs_id, code, text", [
    ("no-existe", 404, "Version not found."),
    ("asof:cs-pub", 404, "Version not found."),
    ("cs-otro", 409, "another project"),
    ("cs-pub", 409, "no longer open"),
    ("cs-closed", 409, "no longer open"),
])
def test_version_que_no_vale(db, cs_id, code, text):
    with pytest.raises(HTTPException) as exc:
        _run(cs_id)
    assert exc.value.status_code == code and text in exc.value.detail


def test_version_ajena_sin_permiso_es_403(db, monkeypatch):
    monkeypatch.setattr(access.auth_service, "resolve_session_user",
                        AsyncMock(return_value={"permissions": {}}))
    with pytest.raises(HTTPException) as exc:
        _run("cs-draft", principal=LUIS)
    assert exc.value.status_code == 403
