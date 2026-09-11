"""Doc 69 §4.9: campos de FACETA aditivos (defaults no-breaking, round-trip)."""
from __future__ import annotations

from app.features.catalog.models import CanonicalColumnDoc, CanonicalTableDoc
from app.features.catalog.schemas import CanonicalColumnBody, CanonicalTableBody


def test_column_facet_defaults():
    c = CanonicalColumnDoc.model_validate({"projectId": "p1", "tableId": "t1", "physicalName": "COD", "logicalName": "codigo", "dataType": "VARCHAR(30)"}).model_dump()
    assert c["logicalDataType"] is None and c["logicalTypeOverridden"] is False
    assert c["logicalOnly"] is False and c["physicalOnly"] is False
    assert c["ordinal"] == 0 and "logicalOrdinal" not in c and "columnOrdinal" not in c   # doc 74: un solo orden


def test_column_facet_roundtrip():
    raw = {"projectId": "p1", "id": "c1", "tableId": "t1", "physicalName": "COD", "logicalName": "codigo",
           "dataType": "VARCHAR(30)", "logicalDataType": "VARCHAR(20)", "logicalTypeOverridden": True,
           "ordinal": 3, "logicalOnly": True, "flgactive": True, "logicalOrdinal": 7, "columnOrdinal": 9}
    d = CanonicalColumnDoc.model_validate(raw).model_dump()
    assert (d["logicalDataType"], d["logicalTypeOverridden"], d["ordinal"], d["logicalOnly"]) == \
        ("VARCHAR(20)", True, 3, True)
    # Doc 74: los órdenes legacy de un doc viejo se descartan al leer.
    assert "flgactive" not in d and "logicalOrdinal" not in d and "columnOrdinal" not in d


def test_table_facet_flags_roundtrip():
    t = CanonicalTableDoc.model_validate({"projectId": "p1", "physicalName": "T", "logicalName": "t"}).model_dump()
    assert t["logicalOnly"] is False and t["physicalOnly"] is False
    t2 = CanonicalTableDoc.model_validate({"projectId": "p1", "physicalName": "T", "logicalName": "t", "physicalOnly": True}).model_dump()
    assert t2["physicalOnly"] is True


def test_bodies_exponen_los_campos():
    b = CanonicalColumnBody.model_validate({"logicalName": "x", "logicalDataType": "DATE", "ordinal": 1})
    assert (b.logicalDataType, b.ordinal, b.logicalTypeOverridden, b.logicalOnly) == ("DATE", 1, None, None)
    tb = CanonicalTableBody.model_validate({"logicalName": "x", "logicalOnly": True})
    assert tb.logicalOnly is True and tb.physicalOnly is None


def test_column_physical_description_doc85():
    base = {"projectId": "p1", "tableId": "t1", "physicalName": "COD", "logicalName": "codigo", "dataType": "VARCHAR(30)"}
    assert CanonicalColumnDoc.model_validate(base).model_dump()["physicalDescription"] is None
    d = CanonicalColumnDoc.model_validate({**base, "description": "Codigo.", "physicalDescription": "Comentario fisico."}).model_dump()
    assert (d["description"], d["physicalDescription"]) == ("Codigo.", "Comentario fisico.")
    assert CanonicalColumnBody.model_validate({"logicalName": "x", "physicalDescription": "c"}).physicalDescription == "c"
    assert CanonicalColumnBody.model_validate({"logicalName": "x"}).physicalDescription is None
