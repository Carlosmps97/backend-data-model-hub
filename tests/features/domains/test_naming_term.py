"""R5b: `namingTerm` aditivo en ParentDomain (invariante de persistencia).

Declarado en el modelo ⇒ persiste en el round-trip (antes se descartaba al leer
por `extra="ignore"`). Default None = no-breaking. El body también lo expone.
"""
from __future__ import annotations

from app.features.domains.models import ParentDomainDoc
from app.features.domains.schemas import ParentDomainBody


def test_naming_term_default_none():
    d = ParentDomainDoc.model_validate({"name": "Monto", "defaultDataType": "decimal(24,4)"})
    assert d.model_dump()["namingTerm"] is None


def test_naming_term_roundtrips():
    raw = {
        "id": "pd1", "name": "Monto", "defaultDataType": "decimal(24,4)",
        "namingTerm": "monto", "flgactive": True,
    }
    dumped = ParentDomainDoc.model_validate(raw).model_dump()
    assert dumped["namingTerm"] == "monto"
    assert "flgactive" not in dumped


def test_body_exposes_naming_term():
    b = ParentDomainBody.model_validate(
        {"name": "Monto", "defaultDataType": "decimal(24,4)", "namingTerm": "monto"}
    )
    assert b.namingTerm == "monto"
    b2 = ParentDomainBody.model_validate({"name": "X", "defaultDataType": "INT"})
    assert b2.namingTerm is None
