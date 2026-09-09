"""Doc 78 §7.1: nombre único CI (409), validación (422), default único, built-in."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.features.bulk_upload.profiles import service
from app.features.bulk_upload.profiles.builtin import BUILTIN_ORIGIN
from app.features.bulk_upload.profiles.models import UploadProfileBody

DEFS = [{"id": "t-p", "name": "Tipo de Entidad", "level": "table", "view": "physical"}]
BODY = UploadProfileBody(name="Mi carga", isDefault=False, sheets={
    "tables": {"name": "T", "mappings": [{"header": "TABLA_LOGICO", "target": {"kind": "field", "field": "logicalName"}}]},
    "columns": {"name": "C", "mappings": [{"header": "TABLA_LOGICO", "target": {"kind": "field", "field": "tableRef"}},
                                          {"header": "CAMPO_LOGICO", "target": {"kind": "field", "field": "logicalName"}}]}})


def _mock(monkeypatch, existing=None):
    monkeypatch.setattr(service.udp_repo, "list_udp", AsyncMock(return_value=DEFS))
    monkeypatch.setattr(service.repository, "list_profiles", AsyncMock(return_value=existing or []))
    created = AsyncMock(side_effect=lambda pid, data: {**data, "id": "new", "projectId": pid})
    monkeypatch.setattr(service.repository, "create_profile", created)
    monkeypatch.setattr(service.repository, "set_default", AsyncMock(side_effect=lambda pid, i: {"id": i, "isDefault": True}))
    return created


def test_create_valida_y_estampa_actor(monkeypatch):
    created = _mock(monkeypatch)
    out = asyncio.run(service.create_profile("ana", "p1", BODY))
    data = created.call_args.args[1]
    assert out["id"] == "new" and (data["createdBy"], data["updatedBy"], data["origin"]) == ("ana", "ana", "user")
    service.repository.set_default.assert_not_awaited()


def test_create_default_true_desmarca_a_los_demas(monkeypatch):
    _mock(monkeypatch)
    out = asyncio.run(service.create_profile("ana", "p1", BODY.model_copy(update={"isDefault": True})))
    assert out == {"id": "new", "isDefault": True}
    service.repository.set_default.assert_awaited_once_with("p1", "new")


def test_create_409_por_nombre_ci_y_422_por_problemas(monkeypatch):
    _mock(monkeypatch, existing=[{"id": "x", "name": "mi CARGA", "isDefault": False, "origin": "user"}])
    with pytest.raises(service.ProfileNameTaken):
        asyncio.run(service.create_profile("ana", "p1", BODY))
    _mock(monkeypatch)
    with pytest.raises(service.ProfileInvalid) as exc:
        asyncio.run(service.create_profile("ana", "p1", BODY.model_copy(update={"name": " "})))
    assert exc.value.problems[0]["code"] == "name-empty"


def test_update_conserva_origin_y_createdby_y_excluye_su_propio_nombre(monkeypatch):
    _mock(monkeypatch, existing=[{"id": "a", "name": "Mi carga", "isDefault": False, "origin": BUILTIN_ORIGIN}])
    monkeypatch.setattr(service.repository, "get_profile",
                        AsyncMock(return_value={"id": "a", "origin": BUILTIN_ORIGIN, "createdBy": "system", "isDefault": False}))
    updated = AsyncMock(side_effect=lambda pid, i, data: {**data, "id": i, "projectId": pid})
    monkeypatch.setattr(service.repository, "update_profile", updated)
    out = asyncio.run(service.update_profile("ana", "p1", "a", BODY))
    data = updated.call_args.args[2]
    assert out["id"] == "a" and (data["origin"], data["createdBy"], data["updatedBy"]) == (BUILTIN_ORIGIN, "system", "ana")
    monkeypatch.setattr(service.repository, "get_profile", AsyncMock(return_value=None))
    assert asyncio.run(service.update_profile("ana", "p1", "zz", BODY)) is None


def test_create_default_materializa_una_sola_vez(monkeypatch):
    created = _mock(monkeypatch)
    doc, warnings = asyncio.run(service.create_default("ana", "p1"))
    data = created.call_args.args[1]
    assert data["origin"] == BUILTIN_ORIGIN and data["isDefault"] is True and doc["isDefault"] is True
    assert warnings and all("doesn't exist" in w or "unmapped" in w for w in warnings)   # DEFS solo trae un UDP
    _mock(monkeypatch, existing=[{"id": "b", "name": "Plantilla BCP", "isDefault": True, "origin": BUILTIN_ORIGIN}])
    assert asyncio.run(service.create_default("ana", "p1")) is None


def test_create_default_no_roba_el_default_existente(monkeypatch):
    created = _mock(monkeypatch, existing=[{"id": "u", "name": "Otro", "isDefault": True, "origin": "user"}])
    asyncio.run(service.create_default("ana", "p1"))
    assert created.call_args.args[1]["isDefault"] is False


def test_validate_y_suggest(monkeypatch):
    _mock(monkeypatch)
    assert asyncio.run(service.validate_body("p1", BODY)) == []
    out = asyncio.run(service.suggest_headers("p1", "tables", ["UDP_Tipo_de_Entidad", "X"]))
    assert out[0]["target"] == {"kind": "udp", "udpIds": ["t-p"]} and out[1]["matched"] is None
    with pytest.raises(ValueError):
        asyncio.run(service.suggest_headers("p1", "nope", []))
