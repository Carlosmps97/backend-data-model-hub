"""Doc 69: las definiciones UDP declaran su faceta (`view`). Default physical
(todas las defs previas son físicas); en level view/canvas se fuerza physical."""
from __future__ import annotations

from app.features.udp.models import UDP_VIEWS, UdpDefinitionDoc
from app.features.udp.repository import _clean


def test_udp_views_tupla():
    assert UDP_VIEWS == ("logical", "physical")


def test_definition_doc_default_physical_y_roundtrip_logical():
    assert UdpDefinitionDoc.model_validate({"projectId": "p1", "name": "X", "level": "table"}).model_dump()["view"] == "physical"
    d = UdpDefinitionDoc.model_validate({"projectId": "p1", "name": "Atributo Cross", "level": "column", "view": "logical",
                                         "dataType": "list", "allowedValues": ["No Definido", "Si", "No"]})
    assert d.model_dump()["view"] == "logical"


def test_clean_normaliza_view():
    assert _clean({"name": "A", "level": "column", "view": "logical", "dataType": "string"})["view"] == "logical"
    assert _clean({"name": "A", "level": "column", "dataType": "string"})["view"] == "physical"
    assert _clean({"name": "A", "level": "column", "view": "weird", "dataType": "string"})["view"] == "physical"
    # Vistas y Model: Erwin solo define UDPs físicos.
    assert _clean({"name": "Tipo de Vista", "level": "view", "view": "logical", "dataType": "list"})["view"] == "physical"
    assert _clean({"name": "Database", "level": "canvas", "view": "logical", "dataType": "string"})["view"] == "physical"
