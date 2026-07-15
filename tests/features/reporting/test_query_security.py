"""Seguridad del motor de reporting: el cursor keyset (base64 JSON del cliente)
va directo al $match, así que NO debe permitir inyectar operadores de Mongo."""
from __future__ import annotations

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
