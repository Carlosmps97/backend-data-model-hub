"""Doc 78 §7.1: rutas de perfiles — códigos HTTP y mapeo de excepciones del
service. Service mockeado; `model.edit` y `alive_project` sobreescritos."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.features.bulk_upload.profiles import service
from app.features.bulk_upload.profiles.router import _can_edit
from app.main import app

DOC = {"id": "a", "projectId": "p1", "name": "P", "isDefault": True, "origin": "user", "sheets": {}, "policies": {}}
BODY = {"name": "P", "isDefault": True, "sheets": {"tables": {"name": "T", "mappings": []}, "columns": {"name": "C", "mappings": []}}}


@pytest.fixture
def api(project_client):
    app.dependency_overrides[_can_edit] = lambda: {"username": "ana"}
    try:
        yield project_client
    finally:
        app.dependency_overrides.pop(_can_edit, None)


def test_list_y_catalogo(api, monkeypatch):
    monkeypatch.setattr(service, "list_profiles", AsyncMock(return_value=[DOC]))
    assert api.get("/api/projects/p1/upload-profiles").json()["data"] == [DOC]
    cat = api.get("/api/projects/p1/upload-profiles/catalog").json()["data"]
    assert "tables" in cat["fields"] and cat["ruleTypes"] and cat["policies"]["onExistingTable"] == ["update", "reject"]


def test_create_201_422_409(api, monkeypatch):
    create = AsyncMock(return_value=DOC)
    monkeypatch.setattr(service, "create_profile", create)
    r = api.post("/api/projects/p1/upload-profiles", json=BODY)
    assert r.status_code == 201 and r.json()["data"] == DOC
    actor, pid, body = create.await_args.args
    assert (actor, pid, body.name, body.sheets["tables"].name) == ("ana", "p1", "P", "T")
    monkeypatch.setattr(service, "create_profile", AsyncMock(side_effect=service.ProfileInvalid([{"path": "name", "code": "name-empty", "message": "m"}])))
    r = api.post("/api/projects/p1/upload-profiles", json=BODY)
    assert r.status_code == 422 and r.json()["detail"]["problems"][0]["code"] == "name-empty"
    monkeypatch.setattr(service, "create_profile", AsyncMock(side_effect=service.ProfileNameTaken()))
    assert api.post("/api/projects/p1/upload-profiles", json=BODY).status_code == 409


def test_get_put_delete_default(api, monkeypatch):
    monkeypatch.setattr(service, "get_profile", AsyncMock(return_value=None))
    assert api.get("/api/projects/p1/upload-profiles/zz").status_code == 404
    monkeypatch.setattr(service, "get_profile", AsyncMock(return_value=DOC))
    assert api.get("/api/projects/p1/upload-profiles/a").json()["data"] == DOC
    monkeypatch.setattr(service, "update_profile", AsyncMock(return_value=DOC))
    assert api.put("/api/projects/p1/upload-profiles/a", json=BODY).json()["data"] == DOC
    monkeypatch.setattr(service, "update_profile", AsyncMock(return_value=None))
    assert api.put("/api/projects/p1/upload-profiles/zz", json=BODY).status_code == 404
    monkeypatch.setattr(service, "delete_profile", AsyncMock(return_value=True))
    assert api.delete("/api/projects/p1/upload-profiles/a").json()["data"] == {"deleted": True}
    monkeypatch.setattr(service, "delete_profile", AsyncMock(return_value=False))
    assert api.delete("/api/projects/p1/upload-profiles/zz").status_code == 404
    monkeypatch.setattr(service, "set_default", AsyncMock(return_value=DOC))
    assert api.post("/api/projects/p1/upload-profiles/a/default").json()["data"]["isDefault"] is True


def test_validate_suggest_y_default(api, monkeypatch):
    monkeypatch.setattr(service, "validate_body", AsyncMock(return_value=[]))
    assert api.post("/api/projects/p1/upload-profiles/validate", json=BODY).json()["data"] == {"problems": []}
    monkeypatch.setattr(service, "suggest_headers", AsyncMock(return_value=[{"header": "X", "target": {"kind": "ignore"}, "matched": None}]))
    r = api.post("/api/projects/p1/upload-profiles/suggest", json={"sheet": "tables", "headers": ["X"]})
    assert r.status_code == 200 and r.json()["data"][0]["header"] == "X"
    assert api.post("/api/projects/p1/upload-profiles/suggest", json={"sheet": "nope", "headers": []}).status_code == 422
    monkeypatch.setattr(service, "create_default", AsyncMock(return_value=(DOC, ["w"])))
    r = api.post("/api/projects/p1/upload-profiles/default")
    assert r.status_code == 201 and r.json()["data"] == {"profile": DOC, "warnings": ["w"]}
    monkeypatch.setattr(service, "create_default", AsyncMock(return_value=None))
    assert api.post("/api/projects/p1/upload-profiles/default").status_code == 409


def test_sin_permiso_es_403(project_client, monkeypatch):
    from app.features.auth.deps import current_principal
    from app.features.auth import service as auth_service
    monkeypatch.setattr(auth_service, "resolve_session_user", AsyncMock(return_value={"permissions": {}}))
    app.dependency_overrides[current_principal] = lambda: type("P", (), {"username": "ana"})()
    try:
        assert project_client.get("/api/projects/p1/upload-profiles").status_code == 403
    finally:
        app.dependency_overrides.pop(current_principal, None)
