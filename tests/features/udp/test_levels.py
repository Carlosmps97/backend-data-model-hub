"""F5 — UDP level 'canvas': la tupla de niveles y la normalización `_clean`
aceptan el nivel del Modelo de Datos (canvas/subject area)."""
from __future__ import annotations

from app.features.udp.models import UDP_LEVELS, UdpDefinitionDoc
from app.features.udp.repository import _clean


def test_udp_levels_incluye_canvas():
    assert "canvas" in UDP_LEVELS


def test_clean_conserva_level_canvas():
    out = _clean({"name": "Dominio funcional", "level": "canvas", "dataType": "string"})
    assert out["level"] == "canvas"


def test_clean_normaliza_level_desconocido_a_column():
    out = _clean({"name": "X", "level": "database", "dataType": "string"})
    assert out["level"] == "column"


def test_definition_doc_roundtrip_canvas():
    d = UdpDefinitionDoc.model_validate({"name": "Dominio funcional", "level": "canvas"})
    assert d.model_dump()["level"] == "canvas"
