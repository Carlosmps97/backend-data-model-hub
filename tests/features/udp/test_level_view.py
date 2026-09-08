"""Doc 61: UDP nivel 'view' — las vistas ganan su propio nivel de UDPs
(Data Standards → UDP → View)."""
from __future__ import annotations

from app.features.udp.models import UDP_LEVELS, UdpDefinitionDoc
from app.features.udp.repository import _clean


def test_udp_levels_incluye_view():
    assert "view" in UDP_LEVELS


def test_clean_conserva_level_view():
    out = _clean({"name": "View Type", "level": "view", "dataType": "list",
                  "allowedValues": ["Regular", "Personalizada"]})
    assert out["level"] == "view"
    assert out["allowedValues"] == ["Regular", "Personalizada"]


def test_definition_doc_roundtrip_view():
    d = UdpDefinitionDoc.model_validate({"projectId": "p1", 
        "name": "View Type", "level": "view", "dataType": "list",
        "allowedValues": ["Regular", "Personalizada"], "defaultValue": "Regular"})
    out = d.model_dump()
    assert out["level"] == "view"
    assert out["defaultValue"] == "Regular"
