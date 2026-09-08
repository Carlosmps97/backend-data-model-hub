"""Settings (naming_config): rutas por proyecto + seeding de defaults (puro)."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.features.settings import repository, service
from app.features.settings.models import DEFAULTS
from app.features.settings.schemas import NamingConfigBody


# ── Rutas registradas (smoke) ────────────────────────────────────────────


def test_settings_routes_registered(client):
    paths = {r.path for r in client.app.routes}
    assert "/api/projects/{project_id}/settings/naming" in paths
    assert "/api/projects/{project_id}/settings/naming/{scope}" in paths
    assert "/api/settings/naming" not in paths


# ── Seeding de defaults (_to_doc es puro, no toca DB) ─────────────────────


def test_to_doc_siembra_default_column_si_no_existe():
    assert repository._to_doc("p1", "column", None) == {
        "scope": "column", "projectId": "p1",
        **DEFAULTS["column"],
    }


def test_to_doc_siembra_default_table_si_no_existe():
    assert repository._to_doc("p1", "table", None)["separator"] == ""
    assert repository._to_doc("p1", "table", None)["case"] == "upper"


def test_to_doc_respeta_doc_existente():
    doc = {"_id": "p1:column", "separator": "-", "case": "lower"}
    out = repository._to_doc("p1", "column", doc)
    assert out == {"scope": "column", "projectId": "p1", "separator": "-", "case": "lower", "maxLength": 150}


def test_to_doc_respeta_maxlength_del_doc():
    out = repository._to_doc("p1", "table", {"_id": "p1:table", "maxLength": 64})
    assert out["maxLength"] == 64 and out["scope"] == "table"


def test_naming_id_por_proyecto_y_scope(monkeypatch):
    """Doc 75 D3: el doc vive por (proyecto, scope) — `_id = <pid>:<scope>`."""
    coll = type("C", (), {})()
    coll.find_one = AsyncMock(return_value=None)
    monkeypatch.setattr(repository, "get_db", AsyncMock(return_value={"naming_config": coll}))
    cfg = asyncio.run(repository.get_one("p1", "column"))
    coll.find_one.assert_awaited_once_with({"_id": "p1:column"})
    assert cfg == {"scope": "column", "separator": "", "case": "upper", "maxLength": 150, "projectId": "p1"}


# ── Validación en el service (agnóstica al store) ────────────────────────


def test_update_naming_rechaza_scope_invalido():
    with pytest.raises(ValueError):
        asyncio.run(service.update_naming("p1", "table_xyz", NamingConfigBody()))


def test_update_naming_rechaza_case_invalido():
    with pytest.raises(ValueError):
        asyncio.run(
            service.update_naming("p1", "column", NamingConfigBody(separator="_", case="title"))
        )
