"""`to_mappings` arma el dict término→abbrev que consume el motor."""
from __future__ import annotations

from app.core.naming import physicalize
from app.features.glossary.service import to_mappings


def test_to_mappings():
    entries = [
        {"id": "a", "term": "monto", "abbrev": "MTO"},
        {"id": "b", "term": "dólares", "abbrev": "USD"},
    ]
    assert to_mappings(entries) == {"monto": "MTO", "dólares": "USD"}


def test_to_mappings_ignora_campos_scope_wordtype():
    # scope/wordType no afectan el mapa término→abbrev que ve el motor.
    entries = [
        {"id": "a", "term": "cuenta", "abbrev": "CTA", "scope": "table", "wordType": "prime"},
    ]
    assert to_mappings(entries) == {"cuenta": "CTA"}


def test_physicalize_per_table_compone_ctariesgo():
    # Composición end-to-end de la regla per-table (screen 08b): términos del
    # scope 'table' + separador '' + UPPER → CTARIESGO (riesgo no mapeado).
    table_entries = [
        {"id": "a", "term": "cuenta", "abbrev": "CTA", "scope": "table"},
    ]
    mappings = to_mappings(table_entries)
    assert physicalize("cuenta riesgo", mappings, separator="", case="upper") == "CTARIESGO"
