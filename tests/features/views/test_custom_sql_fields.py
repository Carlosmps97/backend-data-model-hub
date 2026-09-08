"""Doc 61: campos del modo Personalizada (`customSql` + `customColumns` derivadas)
y `udpValues` de vista — round-trip completo (invariante §2.6)."""
from __future__ import annotations

from app.features.views.models import ViewDoc
from app.features.views.schemas import ViewBody


def test_view_doc_custom_fields_defaults():
    v = ViewDoc.model_validate({"projectId": "p1", "name": "v_x"})
    assert v.customSql is None
    assert v.customColumns == []
    assert v.udpValues == {}


def test_view_doc_custom_fields_roundtrip():
    raw = {"projectId": "p1", 
        "name": "v_x",
        "customSql": "SELECT a AS b FROM t",
        "customColumns": [{"name": "b", "expression": "a"}],
        "udpValues": {"udp-1": "Personalizada"},
    }
    dumped = ViewDoc.model_validate(raw).model_dump(by_alias=True)
    assert dumped["customSql"] == "SELECT a AS b FROM t"
    assert dumped["customColumns"] == [{"name": "b", "expression": "a"}]
    assert dumped["udpValues"] == {"udp-1": "Personalizada"}


def test_view_body_custom_fields_roundtrip():
    body = ViewBody.model_validate({
        "name": "v_x", "customSql": "SELECT 1 AS uno FROM t",
        "customColumns": [{"name": "uno"}], "udpValues": {"udp-1": "Regular"},
    })
    dumped = body.model_dump(by_alias=True)
    assert dumped["customSql"] == "SELECT 1 AS uno FROM t"
    assert dumped["customColumns"] == [{"name": "uno"}]
    assert dumped["udpValues"] == {"udp-1": "Regular"}


def test_view_body_custom_fields_defaults():
    body = ViewBody.model_validate({"name": "v"})
    assert body.customSql is None
    assert body.customColumns == []
    assert body.udpValues == {}
