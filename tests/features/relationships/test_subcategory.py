"""Subcategorías (doc 53): normalización del modelo, gate de payload y diff.

La subcategoría es una VARIANTE de `relationships` (`subcategory=True` +
`subtypeSymbolId`), no una colección nueva: estos tests fijan el contrato de
normalización (ES-UN ⇒ identifying + 1:1) y que los docs legacy no cambian.
"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.features.changesets.diffdetail import entity_detail
from app.features.changesets.validation import payload_error
from app.features.relationships.models import RelationshipDoc


def _base(**over) -> dict:
    d = {"projectId": "p1", "parentTableId": "T1", "childTableId": "T2",
         "pairs": [{"parentColumnId": "c1", "childColumnId": "c2"}]}
    d.update(over)
    return d


class TestSubcategoryShape:
    def test_fuerza_identifying_y_cardinalidades_one_one(self):
        r = RelationshipDoc.model_validate(_base(
            subcategory=True, subtypeSymbolId="sym-1", identifying=False,
            parentCardinality="one", childCardinality="zero-many"))
        assert r.identifying is True
        assert (r.parentCardinality, r.childCardinality) == ("one", "one")
        assert r.subtypeSymbolId == "sym-1"

    def test_sin_simbolo_no_valida(self):
        with pytest.raises(ValidationError, match="subtypeSymbolId"):
            RelationshipDoc.model_validate(_base(subcategory=True))

    def test_simbolo_en_blanco_no_valida(self):
        with pytest.raises(ValidationError, match="subtypeSymbolId"):
            RelationshipDoc.model_validate(_base(subcategory=True, subtypeSymbolId="   "))

    def test_estandar_limpia_simbolo_huerfano(self):
        r = RelationshipDoc.model_validate(_base(subtypeSymbolId="sym-huerfano"))
        assert r.subcategory is False and r.subtypeSymbolId is None

    def test_doc_existente_sin_campos_nuevos_queda_intacto(self):
        r = RelationshipDoc.model_validate(_base(identifying=True,
                                                 childCardinality="one-many"))
        assert r.subcategory is False and r.subtypeSymbolId is None
        assert r.identifying is True and r.childCardinality == "one-many"

    def test_payload_v1_legacy_sigue_normalizando(self):
        r = RelationshipDoc.model_validate({"projectId": "p1", 
            "sourceTableId": "H", "sourceColumnId": "hc",
            "targetTableId": "P", "targetColumnId": "pc",
            "sourceCardinality": "zero-many", "targetCardinality": "one"})
        assert (r.parentTableId, r.childTableId) == ("P", "H")
        assert r.subcategory is False and r.subtypeSymbolId is None

    def test_round_trip_estable(self):
        d1 = RelationshipDoc.model_validate(
            _base(subcategory=True, subtypeSymbolId="s")).model_dump()
        d2 = RelationshipDoc.model_validate(d1).model_dump()
        assert d1 == d2
        assert d1["subcategory"] is True and d1["subtypeSymbolId"] == "s"


class TestGatePayload:
    def test_subcategoria_sin_simbolo_es_error_legible(self):
        err = payload_error("relationships", "r1", "upsert", _base(subcategory=True))
        assert err is not None and "subtypeSymbolId" in err

    def test_subcategoria_valida_pasa(self):
        assert payload_error("relationships", "r1", "upsert",
                             _base(subcategory=True, subtypeSymbolId="s")) is None


class TestDiffDetail:
    def test_created_muestra_subcategory_y_oculta_el_simbolo(self):
        payload = RelationshipDoc.model_validate(
            _base(subcategory=True, subtypeSymbolId="sym-9")).model_dump()
        det = entity_detail("relationships", "r1",
                            {"op": "upsert", "payload": payload}, None,
                            {"tables": {"T1": "PADRE", "T2": "HIJO"}, "columns": {}})
        keys = {f["key"] for f in det["fields"]}
        assert "subcategory" in keys and "subtypeSymbolId" not in keys
        row = next(f for f in det["fields"] if f["key"] == "subcategory")
        assert row["label"] == "Subcategory"
        assert det["name"] == "PADRE → HIJO"

    def test_created_estandar_sin_ruido_nuevo(self):
        payload = RelationshipDoc.model_validate(_base()).model_dump()
        det = entity_detail("relationships", "r1",
                            {"op": "upsert", "payload": payload}, None,
                            {"tables": {}, "columns": {}})
        keys = {f["key"] for f in det["fields"]}
        assert "subcategory" not in keys and "subtypeSymbolId" not in keys
