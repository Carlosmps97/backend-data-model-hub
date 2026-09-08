"""Normalización v1 (source/target) → v2 (parent/child + pairs), doc 19 §3.2."""
from __future__ import annotations

import pytest

from app.features.relationships.models import RelationshipDoc


def _legacy(**over) -> dict:
    base = {"projectId": "p1", 
        "id": "r1",
        "sourceTableId": "tS", "sourceColumnId": "cS",
        "targetTableId": "tT", "targetColumnId": "cT",
        "sourceCardinality": "many", "targetCardinality": "zero-many",
        "identifying": False,
    }
    base.update(over)
    return base


def test_seed_non_identifying_source_es_hijo():
    # Shape del seed viejo: ('many','zero-many') — ambiguo por cardinalidad →
    # gana la convención source=hijo; recupera el zero-many Erwin del extremo padre.
    d = RelationshipDoc.model_validate(_legacy())
    assert d.childTableId == "tS" and d.parentTableId == "tT"
    assert d.pairs[0].parentColumnId == "cT" and d.pairs[0].childColumnId == "cS"
    assert d.parentCardinality == "one" and d.childCardinality == "zero-many"


def test_seed_identifying_source_es_hijo():
    d = RelationshipDoc.model_validate(
        _legacy(sourceCardinality="many", targetCardinality="one", identifying=True))
    assert d.childTableId == "tS" and d.parentTableId == "tT"
    assert d.parentCardinality == "one" and d.childCardinality == "many"
    assert d.identifying is True


def test_dibujada_en_canvas_target_es_hijo():
    # Shape de una relación dibujada a mano pre-v2: source='one', target='many'.
    d = RelationshipDoc.model_validate(
        _legacy(sourceCardinality="one", targetCardinality="many"))
    assert d.childTableId == "tT" and d.parentTableId == "tS"
    assert d.pairs[0].parentColumnId == "cS" and d.pairs[0].childColumnId == "cT"
    assert d.parentCardinality == "one" and d.childCardinality == "many"


def test_sin_cardinalidades_defaults():
    raw = _legacy()
    raw.pop("sourceCardinality")
    raw.pop("targetCardinality")
    d = RelationshipDoc.model_validate(raw)
    assert d.childTableId == "tS"  # ambiguo → source=hijo
    assert d.parentCardinality == "one" and d.childCardinality == "zero-many"


def test_v2_pasa_intacto():
    d = RelationshipDoc.model_validate({"projectId": "p1", 
        "id": "r2", "parentTableId": "tP", "childTableId": "tC",
        "pairs": [
            {"parentColumnId": "p1", "childColumnId": "c1", "roleName": "moneda_soles"},
            {"parentColumnId": "p2", "childColumnId": "c2"},
        ],
        "parentCardinality": "one-only", "childCardinality": "one-many",
        "identifying": True,
    })
    assert [p.roleName for p in d.pairs] == ["moneda_soles", None]
    assert d.parentCardinality == "one-only" and d.childCardinality == "one-many"


def test_pairs_minimo_uno():
    with pytest.raises(Exception):
        RelationshipDoc.model_validate({"projectId": "p1", "parentTableId": "a", "childTableId": "b", "pairs": []})
