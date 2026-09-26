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

import re

from fastapi import HTTPException

from app.core.audit import audit
from app.core.naming import physicalize
from app.features.settings import service as settings_service

from . import repository
from .schemas import AbbreviationBody


def to_mappings(entries: list[dict]) -> dict[str, str]:
    """Arma el dict término→abbrev que consume el motor de naming."""
    return {e["term"]: e["abbrev"] for e in entries}


async def list_entries(project_id: str, scope: str | None = None) -> list[dict]:
    return await repository.list_entries(project_id, scope)


async def create_entry(project_id: str, body: AbbreviationBody) -> dict:
    # Enforcement F2 #1: el término nuevo no puede duplicar el glosario del
    # scope ni aparecer como frase completa en los nombres lógicos publicados
    # (del proyecto, doc 75 D3).
    await ensure_term_valid(project_id, body.term, body.scope)
    return await repository.create_entry(project_id, body.model_dump())


async def update_entry(project_id: str, entry_id: str, body: AbbreviationBody) -> dict | None:
    existing = await repository.get_entry(entry_id)
    if existing is None:
        return None
    if existing.get("locked"):
        raise HTTPException(
            status_code=409,
            detail=(f"The term '{existing['term']}' is locked by an admin; "
                    "unlock it before editing it."))
    # Solo se re-valida si CAMBIA el texto del término (editar la abreviatura
    # no dispara el chequeo de corpus). Se excluye a sí mismo del chequeo de
    # duplicados.
    if body.term.strip().lower() != (existing.get("term") or "").strip().lower():
        await ensure_term_valid(project_id, body.term, body.scope, exclude_id=entry_id)
    return await repository.update_entry(entry_id, body.model_dump())


async def delete_entry(entry_id: str) -> bool:
    existing = await repository.get_entry(entry_id)
    if existing is not None and existing.get("locked"):
        raise HTTPException(
            status_code=409,
            detail=(f"The term '{existing['term']}' is locked by an admin; "
                    "unlock it before deleting it."))
    return await repository.delete_entry(entry_id)


async def physicalize_name(
    project_id: str,
    logical: str,
    scope: str | None = None,
    separator: str | None = None,
) -> str:
    """Físico de `logical` con las reglas del scope (default 'column') DEL
    PROYECTO (doc 75 D3).

    Usa SÓLO los términos de `scope`. Resuelve separador/case desde
    `naming_config[scope]`; un `separator` explícito gana sobre la config."""
    eff_scope = scope or "column"
    cfg = await settings_service.get_naming_for(project_id, eff_scope)
    mappings = to_mappings(await repository.list_entries(project_id, eff_scope))
    sep = separator if separator is not None else cfg["separator"]
    return physicalize(logical, mappings, separator=sep, case=cfg["case"])


# ── Validación de términos (F2 #1): glosario + corpus de nombres lógicos ──

# Límite de "palabra" para la frase completa: cualquier char que NO sea letra
# del español (incluye acentos y ñ) ni dígito. `\b` de re no sirve acá porque
# trata á/é/í/ó/ú/ñ como no-word chars y cortaría dentro de una palabra.
_BOUNDARY = r"[^a-záéíóúñ0-9]"


def corpus_regex(term: str) -> str:
    """Patrón (el caller aplica case-insensitive) que matchea el término como
    FRASE COMPLETA contigua dentro de un nombre lógico. Término escapado con
    re.escape. Puro."""
    esc = re.escape(term.strip())
    return rf"(^|{_BOUNDARY}){esc}({_BOUNDARY}|$)"


def find_glossary_duplicate(term: str, entries: list[dict],
                            exclude_id: str | None = None) -> dict | None:
    """Duplicado EXACTO case-insensitive dentro de los términos del scope
    (`entries` ya viene filtrado por scope). `exclude_id` permite que un update
    no choque contra sí mismo. Puro."""
    needle = term.strip().lower()
    for e in entries:
        if exclude_id is not None and e.get("id") == exclude_id:
            continue
        if (e.get("term") or "").strip().lower() == needle:
            return {"id": e["id"], "term": e["term"], "abbrev": e["abbrev"]}
    return None


async def validate_term(project_id: str, term: str, scope: str, exclude_id: str | None = None,
                        limit: int | None = repository.CORPUS_SAMPLE_CAP) -> dict:
    """Contrato del POST /api/glossary/validate (F2 #1). Chequeo 1: duplicado
    exacto case-insensitive en el glosario del scope. Chequeo 2: frase completa
    contigua en los nombres lógicos publicados — muestra de `limit` o, con
    `limit=None`, la lista COMPLETA (doc 95 D1) — + total. `total` = corpus + 1
    si hay duplicado."""
    entries = await repository.list_entries(project_id, scope)
    dup = find_glossary_duplicate(term, entries, exclude_id)
    corpus, corpus_total = await repository.corpus_conflicts(project_id, corpus_regex(term), scope,
                                                             limit=limit)
    total = corpus_total + (1 if dup else 0)
    return {"ok": total == 0,
            "conflicts": {"glossaryDuplicate": dup, "corpus": corpus, "total": total}}


async def ensure_term_valid(project_id: str, term: str, scope: str,
                            exclude_id: str | None = None) -> None:
    """Enforcement server-side: 409 si el término tiene conflictos. La regla
    vive acá (writes del router); el botón Validar del front es cortesía."""
    result = await validate_term(project_id, term, scope, exclude_id)
    if not result["ok"]:
        total = result["conflicts"]["total"]
        kind = "table" if scope == "table" else "column"
        raise HTTPException(
            status_code=409,
            detail=(f"The term '{term}' can't be added: it already exists in the glossary or "
                    f"appears as a full phrase in logical {kind} names ({total} conflict"
                    f"{'s' if total != 1 else ''}). Adding it would rename those {kind}s."),
        )


async def set_lock(entry_id: str, locked: bool, actor: str) -> dict | None:
    """Bloquea/desbloquea una entrada (solo ADMIN vía router). Audita la acción
    (D4: bloqueada = intocable para todos hasta desbloquear). None si no existe."""
    entry = await repository.set_lock(entry_id, locked, actor)
    if entry is not None:
        await audit(actor, "glossary.lock" if locked else "glossary.unlock",
                    target=entry["term"], target_type="glossary_entry")
    return entry


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

    Re-deriva el físico desde `logicalName` — EXCEPTO las entidades con
    `physicalNameOverridden` (doc 68): un físico custom manda sobre la regla.
    Entidades sin `logicalName` se saltan (no se puede derivar)."""
    out: list[tuple[str, str]] = []
    for e in entities:
        logical = e.get("logicalName")
        if not logical or e.get("physicalNameOverridden"):
            continue
        new_physical = physicalize(logical, mappings, separator=separator, case=case)
        if new_physical != e.get("physicalName"):
            out.append((str(e.get("_id", e.get("id"))), new_physical))
    return out


async def naming_rules(project_id: str, scope: str) -> tuple[dict[str, str], str, str]:
    """Reglas de naming vigentes del scope DEL PROYECTO: (mappings, separator, case).
    Un solo par de lecturas para derivar N nombres (doc 68: el estampado del
    override en changesets las carga UNA vez por lote)."""
    cfg = await settings_service.get_naming_for(project_id, scope)
    mappings = to_mappings(await repository.list_entries(project_id, scope))
    return mappings, cfg["separator"], cfg["case"]


def is_physical_override(payload: dict, mappings: dict[str, str],
                         separator: str, case: str) -> bool:
    """¿El físico del payload es un override manual? Puro (doc 68).

    True si el payload ya viene marcado, o si `physicalName` difiere del
    derivado de `logicalName` con las reglas vigentes. Sin lógico o sin físico
    no hay con qué comparar: manda el flag del payload. El OR nunca degrada un
    custom a derivable; a lo sumo conserva como override un nombre que hoy
    coincide con una derivación vieja (se corrige vaciando el físico)."""
    if payload.get("physicalNameOverridden"):
        return True
    logical = str(payload.get("logicalName") or "").strip()
    physical = str(payload.get("physicalName") or "").strip()
    if not logical or not physical:
        return False
    return physicalize(logical, mappings, separator=separator, case=case) != physical


async def _rephysicalize_scope(project_id: str, scope: str) -> int:
    """Recomputa el físico de todas las entidades de `scope` DEL PROYECTO con la
    config y los términos de ESE scope. Update directo. Devuelve el nº actualizado."""
    cfg = await settings_service.get_naming_for(project_id, scope)
    mappings = to_mappings(await repository.list_entries(project_id, scope))
    entities = await repository.entities_for_rephysicalize(project_id, scope)
    updates = compute_rephysicalize(entities, mappings, cfg["separator"], cfg["case"])
    return await repository.update_physical_names(scope, updates)


# ── Dry-run del re-derivado (docs 94 D7 · 95 D3): qué renombra el borrador y qué ya estaba desfasado ──


def simulate_terms(current: list[dict], upserts: list[dict], deletes: list[str]) -> list[dict]:
    """Términos de UN scope tal como quedarían tras el batch del borrador:
    borra `deletes`, reemplaza texto/abreviatura de los `upserts` con id
    existente y agrega los nuevos. Puro."""
    gone = set(deletes or [])
    by_id = {t.get("id"): dict(t) for t in current if t.get("id") not in gone}
    added: list[dict] = []
    for u in upserts or []:
        term, abbrev = (u.get("term") or "").strip(), (u.get("abbrev") or "").strip()
        if not term or not abbrev:
            continue
        if u.get("id") and u["id"] in by_id:
            by_id[u["id"]].update(term=term, abbrev=abbrev)
        elif not (u.get("id") and u["id"] in gone):
            added.append({"term": term, "abbrev": abbrev})
    return [*by_id.values(), *added]


def classify_rephysicalize(entities: list[dict], current: dict[str, str], draft: dict[str, str],
                           naming: tuple[str, str], draft_naming: tuple[str, str],
                           ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Doc 95 D3 (puro): separa lo que renombra el BORRADOR de lo que ya estaba
    desfasado. `renamed` = el derivado con el borrador difiere del derivado con
    lo vigente; `out_of_sync` = el borrador no lo cambia, pero el nombre guardado
    ya no coincide con la regla (el apply lo repara igual). Mismo filtro que
    `compute_rephysicalize`: sin lógico u override manual ⇒ fuera. La unión de
    ambas listas es exactamente lo que el apply re-deriva."""
    renamed: list[tuple[str, str]] = []
    out_of_sync: list[tuple[str, str]] = []
    for e in entities:
        logical = e.get("logicalName")
        if not logical or e.get("physicalNameOverridden"):
            continue
        new = physicalize(logical, draft, separator=draft_naming[0], case=draft_naming[1])
        if new == e.get("physicalName"):
            continue
        base = physicalize(logical, current, separator=naming[0], case=naming[1])
        (renamed if new != base else out_of_sync).append((str(e.get("_id", e.get("id"))), new))
    return renamed, out_of_sync


def _impact_row(scope: str, e: dict, new: str, names: dict[str, str]) -> dict:
    """Fila del dry-run: una columna lleva el nombre físico de su tabla; una
    tabla se nombra a sí misma."""
    if scope == "column":
        return {"entity": "column", "tableId": e.get("tableId"), "table": names.get(e.get("tableId"), ""),
                "from": e.get("physicalName"), "to": new}
    tid = str(e.get("_id", e.get("id")))
    return {"entity": "table", "tableId": tid, "table": e.get("physicalName"),
            "from": e.get("physicalName"), "to": new}


def _row_order(r: dict) -> tuple:
    return (r["entity"] != "column", (r["table"] or "").lower(), (r["from"] or "").lower())


async def impact_preview(project_id: str, scope: str, terms_upsert: list[dict],
                         terms_delete: list[str], naming: dict | None = None) -> dict:
    """Qué nombres físicos cambiaría aplicar el borrador del glosario en `scope`
    (doc 94 D7). NO escribe. Recorre AMBOS scopes, igual que el apply
    (`rephysicalize` sin scope): el editado con los términos y el naming
    simulados, el otro con lo vigente. Doc 95 D3/D4: devuelve las listas
    COMPLETAS, separadas en `renamed` (lo que cambia por el borrador) y
    `outOfSync` (lo ya desfasado que el apply repara de paso; en el otro scope
    todo cae acá). Fila: `{entity, tableId, table, from, to}`."""
    renamed: list[tuple[str, dict, str]] = []
    out_of_sync: list[tuple[str, dict, str]] = []
    for sc in _REPHYS_SCOPES:
        cfg = await settings_service.get_naming_for(project_id, sc)
        current = await repository.list_entries(project_id, sc)
        cur_naming = (cfg["separator"], cfg["case"])
        draft_terms, draft_naming = current, cur_naming
        if sc == scope:
            draft_terms = simulate_terms(current, terms_upsert, terms_delete)
            if naming:
                draft_naming = (naming["separator"] if naming.get("separator") is not None else cur_naming[0],
                                naming.get("case") or cur_naming[1])
        entities = await repository.entities_for_rephysicalize(project_id, sc)
        by_id = {str(e.get("_id", e.get("id"))): e for e in entities}
        ren, oos = classify_rephysicalize(entities, to_mappings(current), to_mappings(draft_terms),
                                          cur_naming, draft_naming)
        renamed += [(sc, by_id[i], new) for i, new in ren]
        out_of_sync += [(sc, by_id[i], new) for i, new in oos]
    col_tables = [e.get("tableId") for sc, e, _ in renamed + out_of_sync if sc == "column" and e.get("tableId")]
    names = await repository.table_physical_names(col_tables) if col_tables else {}
    return {"renamed": sorted((_impact_row(sc, e, new, names) for sc, e, new in renamed), key=_row_order),
            "outOfSync": sorted((_impact_row(sc, e, new, names) for sc, e, new in out_of_sync), key=_row_order)}


async def rephysicalize(project_id: str, scope: str | None = None) -> dict:
    """Re-deriva el `physicalName` de TODAS las entidades del scope DEL PROYECTO
    a partir de su `logicalName` (engine + naming_config + términos del scope).
    Retroactivo, directo sobre las colecciones publicadas (fuera de publish/changeset).
    Doc 75 D3: un cambio de glosario de UDV jamás re-deriva DDV.

    `scope='table'` → canonical_tables; `'column'` → canonical_columns; sin
    scope → ambos. Devuelve `{updated: {tables, columns}}` (0 en el scope que no
    se tocó). Los físicos con `physicalNameOverridden` NO se tocan (doc 68):
    el override manual predomina sobre la regla del glossary."""
    scopes = (scope,) if scope else _REPHYS_SCOPES
    counts = {"tables": 0, "columns": 0}
    key = {"table": "tables", "column": "columns"}
    for sc in scopes:
        counts[key[sc]] = await _rephysicalize_scope(project_id, sc)
    return {"updated": counts}
