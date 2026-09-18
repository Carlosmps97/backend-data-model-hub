"""Doc 61 → doc 91: `customSql` (User-Defined SQL) se persiste VERBATIM, sin
parse ni columnas derivadas; `udpValues` de vista — round-trip completo
(invariante §2.6). Los campos retirados (`tags`, `filter`, `joinOverride`,
`customColumns`) ya no existen en el modelo: un doc legacy que los traiga los
pierde al validar (extra="ignore")."""
from __future__ import annotations

from app.features.views.models import ViewDoc, normalize_custom_sql
from app.features.views.schemas import ViewBody


def test_view_doc_custom_fields_defaults():
    v = ViewDoc.model_validate({"projectId": "p1", "name": "v_x"})
    assert v.customSql is None
    assert v.udpValues == {}
    assert not hasattr(v, "customColumns")


def test_view_doc_custom_sql_roundtrip_verbatim_aunque_no_parsee():
    raw = {"projectId": "p1", "name": "v_x",
           "customSql": "CREATE VIEW s.v_x AS SELEC a FRM t",   # sintaxis inválida: se guarda igual
           "udpValues": {"udp-1": "Personalizada"}}
    dumped = ViewDoc.model_validate(raw).model_dump(by_alias=True)
    assert dumped["customSql"] == "CREATE VIEW s.v_x AS SELEC a FRM t"
    assert dumped["udpValues"] == {"udp-1": "Personalizada"}
    assert "customColumns" not in dumped


def test_view_doc_descarta_campos_retirados_doc91():
    raw = {"projectId": "p1", "name": "v_x", "tags": [{"key": "k", "value": "v"}],
           "filter": "estado = 'A'", "joinOverride": "t1.id = t2.id",
           "customColumns": [{"name": "zombi"}]}
    dumped = ViewDoc.model_validate(raw).model_dump(by_alias=True)
    for k in ("tags", "filter", "joinOverride", "customColumns"):
        assert k not in dumped


def test_view_body_custom_fields_roundtrip():
    body = ViewBody.model_validate({
        "name": "v_x", "customSql": "SELECT 1 AS uno FROM t", "udpValues": {"udp-1": "Regular"},
    })
    dumped = body.model_dump(by_alias=True)
    assert dumped["customSql"] == "SELECT 1 AS uno FROM t"
    assert dumped["udpValues"] == {"udp-1": "Regular"}
    assert "customColumns" not in dumped and "tags" not in dumped and "filter" not in dumped


def test_view_body_custom_fields_defaults():
    body = ViewBody.model_validate({"name": "v"})
    assert body.customSql is None
    assert body.udpValues == {}


def test_normalize_custom_sql_strip_y_vacio_es_regular():
    assert normalize_custom_sql({"customSql": "  CREATE VIEW x AS SELECT 1 \n"})["customSql"] == "CREATE VIEW x AS SELECT 1"
    assert normalize_custom_sql({"customSql": "   "})["customSql"] is None
    assert normalize_custom_sql({})["customSql"] is None
