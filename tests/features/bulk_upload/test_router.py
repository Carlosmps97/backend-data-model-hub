"""Endpoints de la carga masiva (doc 55 §7): rutas, códigos y mapeo de la
semántica del service a HTTP. El service va mockeado; el permiso `model.edit`
se sobreescribe con `dependency_overrides` (no hay BD en tests)."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient

from app.features.bulk_upload import service
from app.features.bulk_upload.jobs import TooManyJobsError
from app.features.bulk_upload.router import _can_edit
from app.features.bulk_upload.schemas import MAX_CELLS_PER_ROW, MAX_ROWS_PER_SHEET, MAX_SHEETS
from app.main import app

JOB = {"id": "j1", "csId": "c1", "status": "validating", "progress": {"phase": "Queued", "done": 0, "total": 0},
       "report": None, "result": None, "error": None, "fileName": "f.xlsx",
       "createdAt": "2026-08-28T00:00:00+00:00", "updatedAt": "2026-08-28T00:00:00+00:00"}
BODY = {"fileName": "f.xlsx", "profileId": "pf", "sheets": [
    {"name": "Cargar_Tablas", "rows": [{"row": 5, "cells": ["", "TABLA_LOGICO"]}, {"row": 6, "cells": ["", "A"]}]}]}


@pytest.fixture
def client():
    app.dependency_overrides[_can_edit] = lambda: {"username": "ana"}
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(_can_edit, None)


def test_post_crea_job_202(client, monkeypatch):
    start = AsyncMock(return_value=JOB)
    monkeypatch.setattr(service, "start_validation", start)
    r = client.post("/api/changesets/c1/uploads", json=BODY)
    assert r.status_code == 202 and r.json() == {"success": True, "data": JOB}
    cs_id, actor, body = start.await_args.args
    assert (cs_id, actor, body.fileName, body.profileId, body.sheets[0].rows[1].cells) == ("c1", "ana", "f.xlsx", "pf", ["", "A"])


@pytest.mark.parametrize("res,status", [(None, 404), ("forbidden", 403), ("locked", 409), ("profile-not-found", 404)])
def test_post_mapea_guards(client, monkeypatch, res, status):
    monkeypatch.setattr(service, "start_validation", AsyncMock(return_value=res))
    assert client.post("/api/changesets/c1/uploads", json=BODY).status_code == status


def test_post_413_si_excede_los_topes(client, monkeypatch):
    start = AsyncMock(return_value=JOB)
    monkeypatch.setattr(service, "start_validation", start)
    rows = [{"row": 5 + i, "cells": ["A"]} for i in range(MAX_ROWS_PER_SHEET + 1)]
    r = client.post("/api/changesets/c1/uploads", json={"profileId": "pf", "sheets": [{"name": "T", "rows": rows}]})
    assert r.status_code == 413
    sheets = [{"name": f"S{i}", "rows": []} for i in range(MAX_SHEETS + 1)]
    assert client.post("/api/changesets/c1/uploads", json={"profileId": "pf", "sheets": sheets}).status_code == 413
    wide = [{"name": "T", "rows": [{"row": 5, "cells": ["x"] * (MAX_CELLS_PER_ROW + 1)}]}]
    assert client.post("/api/changesets/c1/uploads", json={"profileId": "pf", "sheets": wide}).status_code == 413
    start.assert_not_called()


def test_post_422_sin_profile_id(client, monkeypatch):
    start = AsyncMock(return_value=JOB)
    monkeypatch.setattr(service, "start_validation", start)
    assert client.post("/api/changesets/c1/uploads", json={"sheets": []}).status_code == 422
    start.assert_not_called()


def test_post_429_si_hay_demasiados_jobs(client, monkeypatch):
    monkeypatch.setattr(service, "start_validation", AsyncMock(side_effect=TooManyJobsError("too many")))
    assert client.post("/api/changesets/c1/uploads", json=BODY).status_code == 429


def test_get_job(client, monkeypatch):
    monkeypatch.setattr(service, "get_job", AsyncMock(return_value={**JOB, "status": "validated"}))
    r = client.get("/api/changesets/c1/uploads/j1")
    assert r.status_code == 200 and r.json()["data"]["status"] == "validated"
    service.get_job.assert_awaited_once_with("c1", "ana", "j1")


@pytest.mark.parametrize("res,status", [(None, 404), ("forbidden", 403)])
def test_get_job_mapea(client, monkeypatch, res, status):
    monkeypatch.setattr(service, "get_job", AsyncMock(return_value=res))
    assert client.get("/api/changesets/c1/uploads/j1").status_code == status


def test_apply_202(client, monkeypatch):
    monkeypatch.setattr(service, "start_apply", AsyncMock(return_value={**JOB, "status": "applying"}))
    r = client.post("/api/changesets/c1/uploads/j1/apply")
    assert r.status_code == 202 and r.json()["data"]["status"] == "applying"
    service.start_apply.assert_awaited_once_with("c1", "ana", "j1")


@pytest.mark.parametrize("res,status", [(None, 404), ("forbidden", 403), ("locked", 409),
                                        ("not-validated", 409), ("has-errors", 409), ("busy", 409)])
def test_apply_mapea(client, monkeypatch, res, status):
    monkeypatch.setattr(service, "start_apply", AsyncMock(return_value=res))
    r = client.post("/api/changesets/c1/uploads/j1/apply")
    assert r.status_code == status
    if res in ("not-validated", "has-errors", "busy"):
        assert r.json()["detail"]


def test_delete(client, monkeypatch):
    monkeypatch.setattr(service, "discard", AsyncMock(return_value=True))
    r = client.delete("/api/changesets/c1/uploads/j1")
    assert r.status_code == 200 and r.json()["data"] == {"deleted": True}
    monkeypatch.setattr(service, "discard", AsyncMock(return_value=False))
    assert client.delete("/api/changesets/c1/uploads/j1").status_code == 404
    monkeypatch.setattr(service, "discard", AsyncMock(return_value="forbidden"))
    assert client.delete("/api/changesets/c1/uploads/j1").status_code == 403


def test_delete_409_mientras_aplica(client, monkeypatch):
    monkeypatch.setattr(service, "discard", AsyncMock(return_value="busy"))
    assert client.delete("/api/changesets/c1/uploads/j1").status_code == 409


# ── Doc 87 §3.5: GET …/uploads/targets ────────────────────────────────────
def test_get_targets_no_se_confunde_con_un_job(client, monkeypatch):
    targets = AsyncMock(return_value={"mode": "choose", "candidates": [{"id": "c", "name": "CPYBCA", "folders": 4, "canvases": 0}]})
    get_job = AsyncMock(return_value=JOB)
    monkeypatch.setattr(service, "targets", targets)
    monkeypatch.setattr(service, "get_job", get_job)
    r = client.get("/api/changesets/c1/uploads/targets")
    assert r.status_code == 200 and r.json()["data"]["mode"] == "choose"
    assert targets.await_args.args == ("c1", "ana") and get_job.await_count == 0


@pytest.mark.parametrize("res,status", [(None, 404), ("forbidden", 403), ("locked", 409)])
def test_get_targets_mapea_guards(client, monkeypatch, res, status):
    monkeypatch.setattr(service, "targets", AsyncMock(return_value=res))
    assert client.get("/api/changesets/c1/uploads/targets").status_code == status
