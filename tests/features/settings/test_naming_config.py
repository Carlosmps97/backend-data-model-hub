"""Settings (naming_config): rutas registradas + seeding de defaults (puro)."""
from __future__ import annotations

import pytest

from app.features.settings import repository, service
from app.features.settings.models import DEFAULTS
from app.features.settings.schemas import NamingConfigBody


# ── Rutas registradas (smoke) ────────────────────────────────────────────


def test_settings_routes_registered(client):
    paths = {r.path for r in client.app.routes}
    assert "/api/settings/naming" in paths
    assert "/api/settings/naming/{scope}" in paths


# ── Seeding de defaults (_to_doc es puro, no toca DB) ─────────────────────


def test_to_doc_siembra_default_column_si_no_existe():
    assert repository._to_doc("column", None) == {
        "scope": "column",
        **DEFAULTS["column"],
    }


def test_to_doc_siembra_default_table_si_no_existe():
    assert repository._to_doc("table", None)["separator"] == ""
    assert repository._to_doc("table", None)["case"] == "upper"


def test_to_doc_respeta_doc_existente():
    doc = {"_id": "column", "separator": "-", "case": "lower"}
    out = repository._to_doc("column", doc)
    assert out == {"scope": "column", "separator": "-", "case": "lower", "maxLength": 150}


def test_to_doc_respeta_maxlength_del_doc():
    out = repository._to_doc("table", {"_id": "table", "maxLength": 64})
    assert out["maxLength"] == 64 and out["scope"] == "table"


# ── Validación en el service (agnóstica al store) ────────────────────────


def test_update_naming_rechaza_scope_invalido():
    import asyncio

    with pytest.raises(ValueError):
        asyncio.run(service.update_naming("table_xyz", NamingConfigBody()))


def test_update_naming_rechaza_case_invalido():
    import asyncio

    with pytest.raises(ValueError):
        asyncio.run(
            service.update_naming("column", NamingConfigBody(separator="_", case="title"))
        )
