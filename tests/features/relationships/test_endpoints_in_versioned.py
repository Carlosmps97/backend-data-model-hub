"""relationships y views son contenido de modelo → entran en VERSIONED (changesets)."""
from __future__ import annotations

from app.features.changesets.repository import VERSIONED


def test_relationships_and_views_versioned():
    assert "relationships" in VERSIONED
    assert "views" in VERSIONED


def test_relationship_has_identifying_field():
    from app.features.relationships.models import RelationshipDoc
    r = RelationshipDoc(sourceTableId="a", sourceColumnId="c1", targetTableId="b", targetColumnId="c2")
    assert r.identifying is False


def test_relationship_has_symmetric_cardinality():
    from app.features.relationships.models import RelationshipDoc
    r = RelationshipDoc(sourceTableId="a", sourceColumnId="c1", targetTableId="b", targetColumnId="c2")
    assert r.sourceCardinality == "one"
    assert r.targetCardinality == "many"
