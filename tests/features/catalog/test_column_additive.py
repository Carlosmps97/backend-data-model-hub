"""Campos aditivos de columna canónica (R1a): isNullable / isPartition / description.

Invariante §2.6: declarados en el modelo ⇒ persisten en el round-trip. Defaults
NO-breaking (isNullable=True, isPartition=False, description=None).
"""
from __future__ import annotations

from app.features.catalog.models import CanonicalColumnDoc
from app.features.catalog.schemas import CanonicalColumnBody


def test_column_additive_defaults():
    c = CanonicalColumnDoc.model_validate(
        {"tableId": "t1", "physicalName": "ID", "logicalName": "id", "dataType": "INT"}
    )
    assert c.isNullable is True
    assert c.isPartition is False
    assert c.description is None


def test_column_additive_roundtrip():
    raw = {
        "id": "c1", "tableId": "t1", "physicalName": "FEC", "logicalName": "fecha",
        "dataType": "DATE", "isNullable": False, "isPartition": True,
        "description": "fecha de corte", "flgactive": True,
    }
    dumped = CanonicalColumnDoc.model_validate(raw).model_dump()
    assert dumped["isNullable"] is False
    assert dumped["isPartition"] is True
    assert dumped["description"] == "fecha de corte"
    assert "flgactive" not in dumped


def test_column_body_exposes_additive_fields():
    body = CanonicalColumnBody.model_validate(
        {"logicalName": "fecha", "isPartition": True, "description": "x"}
    )
    assert body.isPartition is True
    assert body.isNullable is True  # default
    assert body.description == "x"
