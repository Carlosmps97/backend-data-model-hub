"""UDP re-physicalize retroactivo (R5): recomputa physicalName desde logicalName.

`compute_rephysicalize` es puro/testeable: re-deriva el físico de TODAS las
entidades desde su `logicalName` con el dict/separador/case del scope y devuelve
SÓLO las que cambian. Smoke de ruta: el endpoint /rephysicalize está registrado.
"""
from __future__ import annotations

from app.features.glossary.service import compute_rephysicalize, to_mappings


# ── compute_rephysicalize: re-deriva desde logical, sólo cambios ───────────


def test_recomputes_from_logical_and_returns_only_changed():
    mappings = to_mappings([
        {"id": "a", "term": "monto", "abbrev": "MTO"},
        {"id": "b", "term": "deuda", "abbrev": "DEU"},
    ])
    entities = [
        # físico desactualizado ⇒ se recomputa desde logical.
        {"_id": "c1", "logicalName": "monto deuda", "physicalName": "STALE"},
        # físico ya consistente con el dict/regla ⇒ NO entra al update.
        {"_id": "c2", "logicalName": "monto", "physicalName": "MTO"},
    ]
    updates = compute_rephysicalize(entities, mappings, separator="_", case="upper")
    assert updates == [("c1", "MTO_DEU")]


def test_uses_scope_separator_and_case():
    # Scope 'table' (screen 08b): separador '' + términos del scope → CTARIESGO
    # (riesgo no mapeado se conserva).
    mappings = to_mappings([{"id": "a", "term": "cuenta", "abbrev": "CTA"}])
    entities = [{"_id": "t1", "logicalName": "cuenta riesgo", "physicalName": "old"}]
    updates = compute_rephysicalize(entities, mappings, separator="", case="upper")
    assert updates == [("t1", "CTARIESGO")]


def test_skips_entities_without_logical():
    mappings = to_mappings([{"id": "a", "term": "monto", "abbrev": "MTO"}])
    entities = [
        {"_id": "c1", "physicalName": "X"},          # sin logicalName → se salta
        {"_id": "c2", "logicalName": "", "physicalName": "Y"},  # vacío → se salta
        {"_id": "c3", "logicalName": "monto", "physicalName": "MTO"},  # sin cambio
    ]
    assert compute_rephysicalize(entities, mappings, separator="_", case="upper") == []


def test_normalizes_id_field_when_no_underscore_id():
    # Acepta docs ya normalizados (`id`) además de los crudos de Mongo (`_id`).
    mappings = to_mappings([{"id": "a", "term": "monto", "abbrev": "MTO"}])
    entities = [{"id": "c1", "logicalName": "monto", "physicalName": "old"}]
    assert compute_rephysicalize(entities, mappings, "_", "upper") == [("c1", "MTO")]


# ── Smoke de ruta: /rephysicalize registrada (POST) ───────────────────────


def test_rephysicalize_route_registered(client):
    paths = {
        (getattr(r, "path", None), m)
        for r in client.app.routes
        for m in getattr(r, "methods", set()) or set()
    }
    assert ("/api/glossary/rephysicalize", "POST") in paths
