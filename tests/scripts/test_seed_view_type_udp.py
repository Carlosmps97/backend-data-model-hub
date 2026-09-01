"""Doc 61: guard de idempotencia del seed (puro, sin DB)."""
from __future__ import annotations

from scripts.seed_view_type_udp import find_existing


def test_nombre_viene_del_catalogo_fijo():
    from scripts.seed_view_type_udp import ALLOWED, DEFAULT, VIEW_TYPE_NAME
    assert VIEW_TYPE_NAME == "Tipo de Vista"
    assert ALLOWED == ["Regular", "Personalizada"] and DEFAULT == "Regular"


def test_find_existing_match_ci():
    defs = [{"id": "u1", "name": "tipo de vista", "level": "view"}]
    assert find_existing(defs)["id"] == "u1"


def test_find_existing_ignora_otros_niveles_y_nombres():
    defs = [{"id": "u1", "name": "Tipo de Vista", "level": "table"},
            {"id": "u2", "name": "Otro", "level": "view"}]
    assert find_existing(defs) is None
