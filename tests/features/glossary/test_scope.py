"""Dictionary R1c: scope en el modelo + ruta acepta ?scope=. Doc 94 D11: el
`wordType` se retiró — un doc o body viejo que lo traiga lo pierde al leer."""
from __future__ import annotations

from app.features.glossary.models import AbbreviationDoc
from app.features.glossary.schemas import AbbreviationBody, PhysicalizeBody


# ── Modelo: invariante de persistencia (campo nuevo persiste con default) ──


def test_doc_default_scope_column():
    d = AbbreviationDoc.model_validate({"projectId": "p1", "term": "monto", "abbrev": "MTO"})
    assert d.model_dump()["scope"] == "column"


def test_doc_persiste_scope_y_descarta_wordtype_viejo():
    d = AbbreviationDoc.model_validate({"projectId": "p1", "term": "cuenta", "abbrev": "CTA", "scope": "table", "wordType": "prime"}
    )
    dumped = d.model_dump()
    assert dumped["scope"] == "table"
    assert "wordType" not in dumped


def test_body_acepta_scope_con_default_y_descarta_wordtype():
    b = AbbreviationBody.model_validate({"term": "x", "abbrev": "X"})
    assert b.scope == "column"
    b2 = AbbreviationBody.model_validate(
        {"term": "y", "abbrev": "Y", "scope": "table", "wordType": "class"}
    )
    assert b2.scope == "table"
    assert "wordType" not in b2.model_dump()


def test_physicalize_body_scope_y_separator_opcionales():
    b = PhysicalizeBody.model_validate({"logical": "monto deuda"})
    assert b.scope is None
    assert b.separator is None
    b2 = PhysicalizeBody.model_validate(
        {"logical": "cuenta riesgo", "scope": "table", "separator": ""}
    )
    assert b2.scope == "table"
    assert b2.separator == ""


# ── Ruta: GET acepta el query param scope (smoke) ─────────────────────────


def test_dictionary_get_acepta_scope_query(client):
    # La ruta sigue registrada y declara el parámetro de query `scope`.
    route = next(
        r for r in client.app.routes if getattr(r, "path", None) == "/api/projects/{project_id}/glossary"
        and "GET" in getattr(r, "methods", set())
    )
    param_names = {p.name for p in route.dependant.query_params}
    assert "scope" in param_names
