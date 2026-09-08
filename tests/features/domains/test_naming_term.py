"""R5b: `namingTerm` aditivo en ParentDomain (invariante de persistencia).

Declarado en el modelo ⇒ persiste en el round-trip (antes se descartaba al leer
por `extra="ignore"`). Default None = no-breaking. El body también lo expone.
"""
from __future__ import annotations

from app.features.domains.models import ParentDomainDoc
from app.features.domains.schemas import ParentDomainBody


def test_naming_term_default_none():
    d = ParentDomainDoc.model_validate({"projectId": "p1", "name": "Monto", "defaultDataType": "decimal(24,4)"})
    assert d.model_dump()["namingTerm"] is None


def test_naming_term_roundtrips():
    raw = {"projectId": "p1", 
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


def test_create_domain_estampa_project_id(monkeypatch):
    """Doc 75 D1: el repositorio estampa el proyecto (nunca viene del cliente)."""
    import asyncio
    from unittest.mock import AsyncMock
    from app.features.domains import repository
    coll = type("C", (), {})(); coll.insert_one = AsyncMock()
    monkeypatch.setattr(repository, "get_db", AsyncMock(return_value={"parent_domains": coll}))
    out = asyncio.run(repository.create_domain("p1", {"name": "Codigo", "defaultDataType": "VARCHAR(20)"}))
    assert out["projectId"] == "p1"
    assert coll.insert_one.call_args.args[0]["projectId"] == "p1"
