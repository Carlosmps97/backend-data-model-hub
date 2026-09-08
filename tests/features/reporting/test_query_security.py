"""Seguridad del motor de reporting: el cursor keyset (base64 JSON del cliente)
va directo al $match, así que NO debe permitir inyectar operadores de Mongo."""
from __future__ import annotations

import asyncio
import base64
import json

import pytest

from app.features.reporting.query.compiler import QueryError
from app.features.reporting.query.executor import _decode_cursor


def _enc(v) -> str:
    return base64.urlsafe_b64encode(json.dumps(v).encode()).decode()


def test_cursor_escalar_valido_ok():
    assert _decode_cursor(_enc(["ABC", "id-1"])) == ["ABC", "id-1"]
    assert _decode_cursor(_enc([123, "id-9"])) == [123, "id-9"]


def test_cursor_con_operador_mongo_rechazado():
    # sort_val = dict → inyectaría {field: {"$ne": null}} (bypass) o {"$regex":…} (ReDoS)
    with pytest.raises(QueryError):
        _decode_cursor(_enc([{"$ne": None}, "id"]))
    with pytest.raises(QueryError):
        _decode_cursor(_enc([{"$regex": "(a+)+$"}, "id"]))


def test_cursor_malformado_rechazado():
    with pytest.raises(QueryError):
        _decode_cursor(_enc(["solo-uno"]))          # longitud != 2
    with pytest.raises(QueryError):
        _decode_cursor(_enc("no-es-lista"))          # no es lista
    with pytest.raises(QueryError):
        _decode_cursor("no-es-base64-json!!")        # no decodea
    with pytest.raises(QueryError):
        _decode_cursor(_enc(["ok", {"$ne": None}]))  # cid no-string


# ── Doc 75: toda consulta del motor es de UN proyecto ──────────────────────


def test_query_spec_exige_project_id():
    from pydantic import ValidationError

    from app.features.reporting.query.spec import QuerySpec
    with pytest.raises(ValidationError):
        QuerySpec.model_validate({"from": "columns"})
    with pytest.raises(ValidationError):
        QuerySpec.model_validate({"from": "columns", "projectId": ""})
    assert QuerySpec.model_validate({"from": "columns", "projectId": "p1"}).projectId == "p1"


def _fake_db():
    from unittest.mock import AsyncMock, MagicMock
    cursor = MagicMock()
    cursor.sort = MagicMock(return_value=cursor)
    cursor.limit = MagicMock(return_value=cursor)
    cursor.max_time_ms = MagicMock(return_value=cursor)
    cursor.to_list = AsyncMock(return_value=[])
    coll = MagicMock()
    coll.find = MagicMock(return_value=cursor)
    coll.aggregate = MagicMock(return_value=cursor)
    return coll, {"canonical_columns": coll, "udp_definitions": coll, "parent_domains": coll,
                  "canonical_tables": coll, "views": coll}


def test_run_query_acota_por_proyecto(monkeypatch):
    from unittest.mock import AsyncMock

    from app.features.reporting.query import executor as ex
    from app.features.reporting.query.spec import QuerySpec
    coll, db = _fake_db()
    monkeypatch.setattr(ex, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(ex, "_udp_defs", AsyncMock(return_value=[]))
    asyncio.run(ex.run_query(QuerySpec.model_validate(
        {"from": "columns", "projectId": "p1", "select": ["physicalName"]})))
    assert coll.find.call_args.args[0]["projectId"] == "p1"
    ex._udp_defs.assert_awaited_once_with("p1")


def test_run_query_agrupado_acota_por_proyecto(monkeypatch):
    from unittest.mock import AsyncMock

    from app.features.reporting.query import executor as ex
    from app.features.reporting.query.spec import QuerySpec
    coll, db = _fake_db()
    monkeypatch.setattr(ex, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(ex, "_udp_defs", AsyncMock(return_value=[]))
    asyncio.run(ex.run_query(QuerySpec.model_validate(
        {"from": "columns", "projectId": "p2", "groupBy": ["dataType"],
         "aggregations": [{"fn": "count", "as": "n"}]})))
    pipe = coll.aggregate.call_args.args[0]
    assert pipe[0]["$match"]["projectId"] == "p2"


def test_view_columns_acota_por_proyecto(monkeypatch):
    from unittest.mock import AsyncMock

    from app.features.reporting.query import executor as ex
    from app.features.reporting.query.spec import QuerySpec
    coll, db = _fake_db()
    monkeypatch.setattr(ex, "get_db", AsyncMock(return_value=db))
    monkeypatch.setattr(ex, "_udp_defs", AsyncMock(return_value=[]))
    asyncio.run(ex.run_query(QuerySpec.model_validate(
        {"from": "view_columns", "projectId": "p3", "select": ["viewName"]})))
    pipe = coll.aggregate.call_args.args[0]
    assert pipe[0]["$match"]["projectId"] == "p3"


def test_udp_defs_lee_solo_el_proyecto(monkeypatch):
    from unittest.mock import AsyncMock

    from app.features.reporting.query import executor as ex
    coll, db = _fake_db()
    monkeypatch.setattr(ex, "get_db", AsyncMock(return_value=db))
    asyncio.run(ex._udp_defs("p1"))
    assert coll.find.call_args.args[0] == {"projectId": "p1", "flgactive": {"$ne": False}}
