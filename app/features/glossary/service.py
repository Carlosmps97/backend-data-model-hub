"""Negocio de `dictionary`: CRUD + conversión lógico↔físico vía el motor.

`physicalize` resuelve separador+case desde `naming_config` (feature `settings`)
del scope pedido y usa SOLO los términos de ese scope.

Precedencia del separador (de mayor a menor):
  1. `separator` explícito en el body (override ad-hoc del frontend para preview),
  2. `naming_config[scope].separator`,
  3. default histórico '_' (si no hay config ni override).
El `case` siempre sale de `naming_config[scope]` (no es overrideable por body
hoy; se mantiene simple). Sin `scope` ⇒ 'column' (compat con el endpoint previo).
"""
from __future__ import annotations

from app.core.naming import logicalize, physicalize
from app.features.settings import service as settings_service

from . import repository
from .schemas import AbbreviationBody


def to_mappings(entries: list[dict]) -> dict[str, str]:
    """Arma el dict término→abbrev que consume el motor de naming."""
    return {e["term"]: e["abbrev"] for e in entries}


async def list_entries(scope: str | None = None) -> list[dict]:
    return await repository.list_entries(scope)


async def create_entry(body: AbbreviationBody) -> dict:
    return await repository.create_entry(body.model_dump())


async def update_entry(entry_id: str, body: AbbreviationBody) -> dict | None:
    return await repository.update_entry(entry_id, body.model_dump())


async def delete_entry(entry_id: str) -> bool:
    return await repository.delete_entry(entry_id)


async def physicalize_name(
    logical: str,
    scope: str | None = None,
    separator: str | None = None,
) -> str:
    """Físico de `logical` con las reglas del scope (default 'column').

    Usa SÓLO los términos de `scope`. Resuelve separador/case desde
    `naming_config[scope]`; un `separator` explícito gana sobre la config."""
    eff_scope = scope or "column"
    cfg = await settings_service.get_naming_for(eff_scope)
    mappings = to_mappings(await repository.list_entries(eff_scope))
    sep = separator if separator is not None else cfg["separator"]
    return physicalize(logical, mappings, separator=sep, case=cfg["case"])


async def logicalize_name(physical: str) -> str:
    mappings = to_mappings(await repository.list_entries())
    return logicalize(physical, mappings)


# ── Re-physicalize retroactivo (R5): recomputa physicalName desde logicalName ──

_REPHYS_SCOPES = ("table", "column")


def compute_rephysicalize(
    entities: list[dict],
    mappings: dict[str, str],
    separator: str,
    case: str,
) -> list[tuple[str, str]]:
    """Puro/testeable: dada la lista de entidades del scope y sus reglas de
    naming, devuelve `[(id, new_physical)]` SÓLO para las que cambian.

    Re-deriva el físico de TODAS desde su `logicalName` (consistencia global del
    glossary: no hay flag de override de físico). Entidades sin `logicalName` se
    saltan (no se puede derivar)."""
    out: list[tuple[str, str]] = []
    for e in entities:
        logical = e.get("logicalName")
        if not logical:
            continue
        new_physical = physicalize(logical, mappings, separator=separator, case=case)
        if new_physical != e.get("physicalName"):
            out.append((str(e.get("_id", e.get("id"))), new_physical))
    return out


async def _rephysicalize_scope(scope: str) -> int:
    """Recomputa el físico de todas las entidades de `scope` con la config y los
    términos de ESE scope. Update directo. Devuelve el nº actualizado."""
    cfg = await settings_service.get_naming_for(scope)
    mappings = to_mappings(await repository.list_entries(scope))
    entities = await repository.entities_for_rephysicalize(scope)
    updates = compute_rephysicalize(entities, mappings, cfg["separator"], cfg["case"])
    return await repository.update_physical_names(scope, updates)


async def rephysicalize(scope: str | None = None) -> dict:
    """Re-deriva el `physicalName` de TODAS las entidades del scope a partir de
    su `logicalName` (engine + naming_config + términos del scope). Retroactivo,
    directo sobre las colecciones publicadas (fuera de publish/changeset).

    `scope='table'` → canonical_tables; `'column'` → canonical_columns; sin
    scope → ambos. Devuelve `{updated: {tables, columns}}` (0 en el scope que no
    se tocó). Re-deriva TODOS por consistencia global del glossary; no existe
    flag de override del físico."""
    scopes = (scope,) if scope else _REPHYS_SCOPES
    counts = {"tables": 0, "columns": 0}
    key = {"table": "tables", "column": "columns"}
    for sc in scopes:
        counts[key[sc]] = await _rephysicalize_scope(sc)
    return {"updated": counts}
