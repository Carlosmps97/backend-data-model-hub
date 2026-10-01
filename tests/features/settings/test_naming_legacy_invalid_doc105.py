"""Doc 105 (revisión R2): `standards/apply` grababa el naming sin validar
(ahora da 422). Una config inválida que ya quedó guardada (por API), o que
vuelva por un rollback/copia de estándares, hacía que el motor levantara
ValueError: cada alta/edición de columna en un draft y `physicalize` daban
500. La lectura la tolera: lo inválido cae al default del scope."""
from __future__ import annotations

import asyncio

import pytest

from app.core.db import client as db_client
from app.core.scope import naming_id
from app.features.settings import repository
from tests.support.fakedb import FakeDb


@pytest.fixture
def db(monkeypatch) -> FakeDb:
    fake = FakeDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    return fake


def test_valores_invalidos_guardados_caen_al_default(db):
    db.raw["naming_config"].insert_one({"_id": naming_id("p1", "column"), "projectId": "p1", "scope": "column",
                                        "separator": "_", "case": "snake", "maxLength": -5})
    got = asyncio.run(repository.get_one("p1", "column"))
    assert (got["separator"], got["case"], got["maxLength"]) == ("_", "upper", 150)


def test_valores_validos_se_respetan(db):
    db.raw["naming_config"].insert_one({"_id": naming_id("p1", "table"), "projectId": "p1", "scope": "table",
                                        "separator": "", "case": "camel", "maxLength": 30})
    got = asyncio.run(repository.get_all("p1"))["table"]
    assert (got["case"], got["maxLength"]) == ("camel", 30)


def test_maxlength_0_es_limite_desactivado_y_se_lee_tal_cual(db):
    db.raw["naming_config"].insert_one({"_id": naming_id("p1", "column"), "projectId": "p1", "scope": "column",
                                        "separator": "", "case": "upper", "maxLength": 0})
    assert asyncio.run(repository.get_one("p1", "column"))["maxLength"] == 0
