"""Campos aditivos del editor de vista (R4c-backend, 07b-2).

Invariante §2.6: declarados en model + schema ⇒ persisten en el round-trip.
Defaults NO-breaking (todos opcionales). `schema` viaja vía alias (igual que
`catalog`); el atributo interno es `sql_schema`.
"""
from __future__ import annotations

from app.features.views.models import ViewDoc
from app.features.views.schemas import ViewBody


def test_view_additive_defaults():
    v = ViewDoc.model_validate({"projectId": "p1", "name": "v_cuenta"})
    assert v.tableId is None
    assert v.sql_schema is None
    assert v.tags == []
    assert v.filter is None
    assert v.sources == []
    assert v.outputAlias is None
    assert v.expression is None
    # No rompe lo existente.
    assert v.sql == ""
    assert v.description is None


def test_view_doc_schema_alias_roundtrip():
    raw = {"projectId": "p1", 
        "id": "v1", "name": "v_cuenta", "schema": "core_v", "tableId": "t1",
        "tags": ["pii", "gold"], "filter": "estado = 'A'",
        "sources": [{"table": "cuenta", "outputAlias": "ctas", "expression": "id"}],
        "outputAlias": "v_ctas", "expression": "id + 1",
        "sql": "SELECT 1", "description": "vista de cuentas", "flgactive": True,
    }
    dumped = ViewDoc.model_validate(raw).model_dump(by_alias=True)
    # `schema` (no `sql_schema`) en la salida serializada.
    assert dumped["schema"] == "core_v"
    assert "sql_schema" not in dumped
    assert dumped["tableId"] == "t1"
    assert dumped["tags"] == ["pii", "gold"]
    assert dumped["filter"] == "estado = 'A'"
    assert dumped["sources"][0]["outputAlias"] == "ctas"
    assert dumped["outputAlias"] == "v_ctas"
    assert dumped["expression"] == "id + 1"
    assert dumped["sql"] == "SELECT 1"
    assert dumped["description"] == "vista de cuentas"
    # Campos internos de Mongo descartados por extra="ignore".
    assert "flgactive" not in dumped


def test_view_doc_accepts_sql_schema_by_name():
    # populate_by_name: también acepta el nombre interno en la entrada.
    v = ViewDoc.model_validate({"projectId": "p1", "name": "v", "sql_schema": "raw_v"})
    assert v.sql_schema == "raw_v"
    assert v.model_dump(by_alias=True)["schema"] == "raw_v"


def test_view_body_exposes_additive_fields():
    body = ViewBody.model_validate({
        "name": "v_cuenta", "schema": "core_v", "tableId": "t1",
        "tags": ["x"], "filter": "1=1",
        "sources": [{"table": "cuenta"}], "outputAlias": "a", "expression": "e",
    })
    dumped = body.model_dump(by_alias=True)
    assert dumped["schema"] == "core_v"
    assert dumped["tableId"] == "t1"
    assert dumped["tags"] == ["x"]
    assert dumped["filter"] == "1=1"
    assert dumped["sources"] == [{"table": "cuenta"}]
    assert dumped["outputAlias"] == "a"
    assert dumped["expression"] == "e"


def test_view_body_additive_defaults():
    body = ViewBody.model_validate({"name": "v"})
    assert body.tableId is None
    assert body.sql_schema is None
    assert body.tags == []
    assert body.filter is None
    assert body.sources == []
    assert body.outputAlias is None
    assert body.expression is None
