"""Campos aditivos de columna canónica (R1a): isNullable / isPartition / description.

Invariante §2.6: declarados en el modelo ⇒ persisten en el round-trip. Defaults
NO-breaking (isNullable=True, isPartition=False, description=None).
"""
from __future__ import annotations

from app.features.catalog.models import CanonicalColumnDoc
from app.features.catalog.schemas import CanonicalColumnBody


def test_column_additive_defaults():
    c = CanonicalColumnDoc.model_validate({"projectId": "p1", "tableId": "t1", "physicalName": "ID", "logicalName": "id", "dataType": "INT"}
    )
    assert c.isNullable is True
    assert c.isPartition is False
    assert c.description is None


def test_column_additive_roundtrip():
    raw = {"projectId": "p1", 
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


def test_docs_de_alcance_exigen_project_id():
    """Doc 75 D1/D2: projectId obligatorio en los docs de alcance; changeset de UN proyecto."""
    from pydantic import ValidationError
    import pytest
    from app.features.catalog.models import CanonicalTableDoc
    from app.features.changesets.models import ChangesetDoc
    from app.features.data_standards.models import KINDS, StandardsVersionDoc
    from app.features.projects.models import ProjectDoc
    with pytest.raises(ValidationError):
        CanonicalTableDoc.model_validate({"physicalName": "T", "logicalName": "t"})
    t = CanonicalTableDoc.model_validate({"projectId": "p1", "physicalName": "T", "logicalName": "t", "projectId": "p1"})
    assert t.projectId == "p1"
    cs = ChangesetDoc.model_validate({"title": "x", "owner": "ana", "projectId": "p1"})
    assert cs.projectId == "p1" and not hasattr(cs, "projectIds")
    assert "copy" in KINDS
    v = StandardsVersionDoc.model_validate({"projectId": "p1", "id": "v", "seq": 1, "label": "v1", "title": "t",
                                            "author": "a", "projectId": "p1"})
    assert v.projectId == "p1"
    p = ProjectDoc.model_validate({"name": "DDV"})
    assert p.description is None and not hasattr(p, "family")
