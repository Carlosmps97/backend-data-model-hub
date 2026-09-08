"""relationships y views son contenido de modelo → entran en VERSIONED (changesets)."""
from __future__ import annotations

from app.features.changesets.repository import VERSIONED


def test_relationships_and_views_versioned():
    assert "relationships" in VERSIONED
    assert "views" in VERSIONED


def test_relationship_defaults_v2():
    from app.features.relationships.models import RelationshipDoc
    r = RelationshipDoc(projectId="p1", parentTableId="a", childTableId="b",
                        pairs=[{"parentColumnId": "p1", "childColumnId": "c1"}])
    assert r.identifying is False
    assert r.parentCardinality == "one"
    assert r.childCardinality == "zero-many"


def test_relationship_legacy_kwargs_se_normalizan():
    from app.features.relationships.models import RelationshipDoc
    r = RelationshipDoc(projectId="p1", sourceTableId="a", sourceColumnId="c1",
                        targetTableId="b", targetColumnId="c2")
    # sin cardinalidades → ambiguo → source=hijo (convención seed)
    assert r.childTableId == "a" and r.parentTableId == "b"
    assert r.pairs[0].childColumnId == "c1" and r.pairs[0].parentColumnId == "c2"
