"""Backfill v1 → v2 de `relationships` (doc 19).

Convierte docs con shape legacy (sourceTableId/targetTableId + 1 par de
columnas) al shape v2 (parentTableId/childTableId + `pairs[]`), usando la
MISMA regla de orientación que el validator de `RelationshipDoc`:

  1. si EXACTAMENTE un extremo tiene cardinalidad many-ish → ese es el hijo;
  2. ambiguo → hijo = source (convención seed/migración, medida 175/175
     contra los flags FK de la BD el 2026-07-16).

Acá además se contrasta con los flags PK/FK reales de las columnas: si son
concluyentes (FK en un solo extremo) y CONTRADICEN la regla, ganan los flags
y se loguea la discrepancia.

También reescribe los payloads de relationships de `changeset_changes` de
changesets en DRAFT (los publicados son historia; sus before-images legacy
las normaliza el validator si un rollback las re-registra).

Idempotente: docs que ya tienen `pairs` no se tocan. Sin `--apply` es dry-run.

Uso (desde la raíz del backend):
  .venv/bin/python -m scripts.backfill_relationships_v2           # dry-run
  .venv/bin/python -m scripts.backfill_relationships_v2 --apply
"""
from __future__ import annotations

import argparse
import os
from collections import Counter
from datetime import datetime, timezone

from app.features.relationships.models import (  # reglas compartidas con el validator
    _MANYISH,
    _ONEISH,
    _child_cardinality,
)

ACTIVE = {"flgactive": {"$ne": False}}
LEGACY = ("sourceTableId", "sourceColumnId", "targetTableId",
          "targetColumnId", "sourceCardinality", "targetCardinality")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _convert(d: dict, child_is_source: bool) -> dict:
    """Campos v2 a partir de un doc/payload legacy, con orientación dada."""
    s_card, t_card = str(d.get("sourceCardinality") or ""), str(d.get("targetCardinality") or "")
    if child_is_source:
        parent_t, parent_c = d.get("targetTableId"), d.get("targetColumnId")
        child_t, child_c = d.get("sourceTableId"), d.get("sourceColumnId")
        parent_raw, child_raw = t_card, s_card
    else:
        parent_t, parent_c = d.get("sourceTableId"), d.get("sourceColumnId")
        child_t, child_c = d.get("targetTableId"), d.get("targetColumnId")
        parent_raw, child_raw = s_card, t_card
    return {
        "parentTableId": parent_t, "childTableId": child_t,
        "pairs": [{"parentColumnId": parent_c, "childColumnId": child_c, "roleName": None}],
        "parentCardinality": parent_raw if parent_raw in _ONEISH else "one",
        "childCardinality": _child_cardinality(child_raw, parent_raw),
    }


def _orient(d: dict, key_flags: dict) -> tuple[bool, bool]:
    """(child_is_source, discrepancia_flags_vs_regla)."""
    s_many = str(d.get("sourceCardinality") or "") in _MANYISH
    t_many = str(d.get("targetCardinality") or "") in _MANYISH
    by_rule = s_many if s_many != t_many else True
    s_fk = bool((key_flags.get(d.get("sourceColumnId")) or {}).get("isForeignKey"))
    t_fk = bool((key_flags.get(d.get("targetColumnId")) or {}).get("isForeignKey"))
    if s_fk != t_fk:                 # flags concluyentes → mandan
        by_flags = s_fk
        return by_flags, by_flags != by_rule
    return by_rule, False


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill relationships v1→v2 (doc 19)")
    ap.add_argument("--apply", action="store_true", help="escribir (sin esto: dry-run)")
    args = ap.parse_args()

    from dotenv import load_dotenv

    from app.core.db.sync import get_sync_db
    load_dotenv()
    db = get_sync_db()

    key_flags = {c["_id"]: c for c in db.canonical_columns.find(
        ACTIVE, {"isPrimaryKey": 1, "isForeignKey": 1})}

    stats: Counter = Counter()

    # ── 1 · colección relationships ────────────────────────────────────────
    for r in db.relationships.find({}):  # también inactivas: shape uniforme
        if r.get("pairs") or not r.get("sourceTableId"):
            stats["ya v2 (sin tocar)"] += 1
            continue
        child_is_source, disc = _orient(r, key_flags)
        fields = _convert(r, child_is_source)
        stats["convertidas"] += 1
        stats["hijo=source"] += child_is_source
        stats["hijo=target"] += (not child_is_source)
        if disc:
            stats["discrepancia flags vs regla (ganaron flags)"] += 1
            print(f"  ⚠ {r['_id']}: la cardinalidad sugería lo contrario; se usó el flag FK")
        if args.apply:
            db.relationships.update_one(
                {"_id": r["_id"]},
                {"$set": {**fields, "updatedAt": _now()},
                 "$unset": {k: "" for k in LEGACY}})

    # ── 2 · payloads de drafts abiertos ─────────────────────────────────────
    draft_ids = {c["_id"] for c in db.changesets.find({"status": "draft"}, {"_id": 1})}
    for ch in db.changeset_changes.find({"collection": "relationships"}):
        if ch.get("csId") not in draft_ids:
            continue
        p = ch.get("payload") or {}
        if ch.get("op") == "delete" or p.get("pairs") or not p.get("sourceTableId"):
            continue
        child_is_source, _ = _orient(p, key_flags)
        newp = {**{k: v for k, v in p.items() if k not in LEGACY},
                **_convert(p, child_is_source)}
        stats["payloads de draft convertidos"] += 1
        if args.apply:
            db.changeset_changes.update_one({"_id": ch["_id"]}, {"$set": {"payload": newp}})

    print(f"\n{'APLICADO' if args.apply else 'DRY-RUN (nada escrito; usar --apply)'}")
    for k, v in sorted(stats.items()):
        print(f"  {k}: {v}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
