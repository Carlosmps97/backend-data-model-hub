"""Doc 105 (revisión R5, punto 4) — un naming inválido no vuelve por ROLLBACK.

`restore_naming` regrababa el snapshot tal cual: un snapshot anterior al doc
105 con un valor que el motor no puede usar (p. ej. `case: 'Upper'`, que el
apply de entonces aceptaba) volvía a la BD. La app lo tolera al leer
(`settings/repository._usable`), pero el kit Erwin lee `naming_config` directo
y abortaba. Ahora el restore omite esos valores con la MISMA regla de lectura
de la app (0 en `maxLength` = sin tope: válido)."""
from __future__ import annotations

import asyncio

from app.core.db import client as db_client
from app.features.data_standards import repository as std_repo
from app.features.settings import repository as set_repo
from tests.support.fakedb import FakeDb


def _stored(fake: FakeDb, scope: str) -> tuple:
    d = fake.raw["naming_config"].find_one({"_id": f"p1:{scope}"}) or {}
    return d.get("separator"), d.get("case"), d.get("maxLength")


def test_restore_naming_omite_los_valores_que_la_app_no_puede_usar(monkeypatch):
    fake = FakeDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    fake.raw["naming_config"].insert_one({"_id": "p1:column", "projectId": "p1", "scope": "column",
                                          "separator": "", "case": "lower", "maxLength": 60})
    snap = {"column": {"separator": 5, "case": "Upper", "maxLength": -1},       # nada usable
            "table": {"separator": "_", "case": "Camel", "maxLength": None}}    # sólo el separador
    asyncio.run(std_repo.restore_naming("p1", snap))
    # Ronda 3: lo inválido o ausente del snapshot NO vuelve a la BD y tampoco deja
    # el valor de ahora: se restaura lo que la app leía de ese snapshot = el
    # default del scope (un snapshot legado con `maxLength: None` valía 150).
    assert _stored(fake, "column") == (None, None, None)
    assert _stored(fake, "table") == ("_", None, None)
    col = asyncio.run(set_repo.get_one("p1", "column"))
    assert (col["separator"], col["case"], col["maxLength"]) == ("", "upper", 150)
    assert asyncio.run(set_repo.get_one("p1", "table"))["case"] == "upper"       # la lectura: default


def test_restore_naming_valido_se_restaura_completo_con_maxlength_cero(monkeypatch):
    fake = FakeDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    asyncio.run(std_repo.restore_naming("p1", {"column": {"separator": "_", "case": "camel", "maxLength": 0}}))
    assert _stored(fake, "column") == ("_", "camel", 0)                          # 0 = sin tope: válido
