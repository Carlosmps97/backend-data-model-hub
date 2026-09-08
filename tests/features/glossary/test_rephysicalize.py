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
    assert ("/api/projects/{project_id}/glossary/rephysicalize", "POST") in paths


# ── Override del físico (doc 68): el flag persistido corta la re-derivación ─


def test_skips_entities_with_physical_override():
    # Un físico custom (marcado) NO se re-deriva aunque difiera de la regla;
    # el resto sí. Es la capa defensiva del fix doc 68 (el repository ya filtra
    # los marcados en la query, esto cubre callers que pasen listas crudas).
    mappings = to_mappings([{"id": "a", "term": "monto", "abbrev": "MTO"}])
    entities = [
        {"_id": "c1", "logicalName": "monto", "physicalName": "HD_MONTO_X",
         "physicalNameOverridden": True},
        {"_id": "c2", "logicalName": "monto", "physicalName": "STALE",
         "physicalNameOverridden": False},
        {"_id": "c3", "logicalName": "monto", "physicalName": "OLD"},
    ]
    updates = compute_rephysicalize(entities, mappings, separator="", case="upper")
    assert updates == [("c2", "MTO"), ("c3", "MTO")]


# ── is_physical_override (doc 68): inferencia pura del flag ────────────────

from app.features.glossary.service import is_physical_override


def test_is_physical_override_infers_from_mismatch():
    m = to_mappings([{"id": "a", "term": "monto", "abbrev": "MTO"}])
    # físico ≠ derivado ⇒ override (aunque el payload diga False).
    assert is_physical_override(
        {"logicalName": "monto", "physicalName": "HD_MTO"}, m, "", "upper") is True
    assert is_physical_override(
        {"logicalName": "monto", "physicalName": "HD_MTO",
         "physicalNameOverridden": False}, m, "", "upper") is True
    # físico == derivado ⇒ no-override.
    assert is_physical_override(
        {"logicalName": "monto", "physicalName": "MTO"}, m, "", "upper") is False


def test_is_physical_override_respects_explicit_true_and_missing_names():
    m = to_mappings([{"id": "a", "term": "monto", "abbrev": "MTO"}])
    # Marcado explícito ⇒ True aunque coincida con la derivación.
    assert is_physical_override(
        {"logicalName": "monto", "physicalName": "MTO",
         "physicalNameOverridden": True}, m, "", "upper") is True
    # Sin lógico o sin físico ⇒ no hay con qué comparar: manda el payload.
    assert is_physical_override({"physicalName": "X"}, m, "", "upper") is False
    assert is_physical_override({"logicalName": "monto", "physicalName": ""},
                                m, "", "upper") is False
    assert is_physical_override({"physicalName": "X",
                                 "physicalNameOverridden": True}, m, "", "upper") is True


def test_entities_for_rephysicalize_filtra_por_proyecto(monkeypatch):
    """Doc 75 D3: el barrido de re-physicalize es POR PROYECTO."""
    import asyncio
    from unittest.mock import AsyncMock, MagicMock
    from app.features.glossary import repository
    cursor = MagicMock(); cursor.to_list = AsyncMock(return_value=[])
    coll = MagicMock(); coll.find = MagicMock(return_value=cursor)
    monkeypatch.setattr(repository, "get_db", AsyncMock(return_value={"canonical_columns": coll}))
    asyncio.run(repository.entities_for_rephysicalize("p1", "column"))
    flt = coll.find.call_args.args[0]
    assert flt["projectId"] == "p1" and flt["physicalNameOverridden"] == {"$ne": True}
