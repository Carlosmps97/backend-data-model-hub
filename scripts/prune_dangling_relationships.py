"""One-shot maintenance — purge relationships whose column refs don't resolve.

WHY
    After the column-id migration (see `backfill_column_ids.py`),
    `Relationship.sourceColumn` and `Relationship.targetColumn` should
    hold the stable `TableColumn.id` (UUIDv4). However, two classes of
    legacy / corrupted data can sneak in:

      1. Old relationships that still carry the column **name** instead
         of the id — these point at handles that no longer exist.

      2. Mangled refs such as `<col-id>-target` (the React Flow handle
         suffix accidentally stored as the column ref). The new canvas
         then re-suffixes it to `<col-id>-target-source` when building
         the edge handle id, which React Flow can never resolve, so it
         logs `error#008 — Couldn't create edge`.

    Both produce noise in the browser console and ghost edges that can
    never render. This script walks every model and **deletes** any
    relationship whose source or target column ref doesn't resolve to a
    real `column.id` (preferred) or `column.name` (legacy fallback) on
    the corresponding table.

WHAT IT DOES
    1. Loads every active document from `model_tables` and indexes its
       columns by `(modelId, tableId)` AND `(modelId, tableName)` →
       `{ ids: set, names: set }`.
    2. Walks every active document in `model_relationships`. For each
       one, resolves the source and target table (by id OR name) and
       checks the column ref against the table's known column ids and
       names.
    3. Reports every dangling relationship to stdout with enough
       context to verify what is about to be removed.
    4. With `--apply`, soft-deletes the offending docs (sets
       `flgactive=False` plus `deletedAt`) — same convention used by
       `models_db.delete_model`. With the default dry-run, no writes
       happen.

SAFETY
    - Idempotent: a second run reports zero candidates.
    - Soft-delete only — nothing is permanently dropped from Cosmos.
      Re-activating is a one-line `update_many` if the user finds the
      script over-removed.
    - Never touches tables, columns, models, views, or projects.

USAGE
    From the `agent-modeler/` repo root with the virtualenv active:

        python -m scripts.prune_dangling_relationships          # dry run
        python -m scripts.prune_dangling_relationships --apply  # purge

    Reads `COSMOS_CONNECTION_STRING` and `COSMOS_DATABASE` from the
    environment (same vars the FastAPI backend uses).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Ensure `src.*` is importable when invoked as a module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db.motor_client import (  # noqa: E402
    connect as motor_connect,
    disconnect as motor_disconnect,
    get_db,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─────────────────────────────────────────────────────────────────────
# Index helpers
# ─────────────────────────────────────────────────────────────────────

# (modelId, key) → { "ids": set[str], "names": set[str] }, where `key`
# is either the table's `id` or its `name`. Both are accepted because
# legacy relationships may store either.
TableIndex = dict[tuple[str, str], dict[str, set[str]]]


async def _build_table_index(db: Any) -> TableIndex:
    coll = db["model_tables"]
    index: TableIndex = {}

    cursor = coll.find({"flgactive": {"$ne": False}})
    async for doc in cursor:
        model_id = doc.get("modelId")
        table_id = doc.get("_id") or doc.get("id")
        table_name = doc.get("name")
        if not isinstance(model_id, str) or not isinstance(table_id, str):
            continue

        col_ids: set[str] = set()
        col_names: set[str] = set()
        for col in doc.get("columns") or []:
            if not isinstance(col, dict):
                continue
            cid = col.get("id")
            cname = col.get("name")
            if isinstance(cid, str) and cid:
                col_ids.add(cid)
            if isinstance(cname, str) and cname:
                col_names.add(cname)

        bucket = {"ids": col_ids, "names": col_names}
        index[(model_id, table_id)] = bucket
        if isinstance(table_name, str) and table_name:
            # Same bucket reachable by name too — relationships can use
            # either form.
            index[(model_id, table_name)] = bucket

    return index


def _column_resolves(
    bucket: dict[str, set[str]] | None,
    ref: Any,
) -> bool:
    """True iff `ref` matches a known column id or name on the table."""
    if bucket is None:
        return False
    if not isinstance(ref, str) or not ref:
        return False
    return ref in bucket["ids"] or ref in bucket["names"]


# ─────────────────────────────────────────────────────────────────────
# Scan + delete
# ─────────────────────────────────────────────────────────────────────


async def _scan_dangling(db: Any, table_index: TableIndex) -> list[dict[str, Any]]:
    """Return every relationship whose endpoints don't resolve."""
    coll = db["model_relationships"]
    candidates: list[dict[str, Any]] = []

    cursor = coll.find({"flgactive": {"$ne": False}})
    async for rel in cursor:
        model_id = rel.get("modelId")
        if not isinstance(model_id, str):
            # Orphan rel without a model — definitely dangling.
            candidates.append(rel)
            continue

        src_table_ref = rel.get("sourceTable")
        tgt_table_ref = rel.get("targetTable")
        src_col_ref = rel.get("sourceColumn")
        tgt_col_ref = rel.get("targetColumn")

        src_bucket = table_index.get((model_id, src_table_ref)) if isinstance(src_table_ref, str) else None
        tgt_bucket = table_index.get((model_id, tgt_table_ref)) if isinstance(tgt_table_ref, str) else None

        src_ok = _column_resolves(src_bucket, src_col_ref)
        tgt_ok = _column_resolves(tgt_bucket, tgt_col_ref)

        if src_ok and tgt_ok:
            continue

        rel["_dangling_reason"] = {
            "source_table_known": src_bucket is not None,
            "source_column_resolves": src_ok,
            "target_table_known": tgt_bucket is not None,
            "target_column_resolves": tgt_ok,
        }
        candidates.append(rel)

    return candidates


async def _soft_delete(db: Any, ids: list[str]) -> int:
    if not ids:
        return 0
    result = await db["model_relationships"].update_many(
        {"_id": {"$in": ids}},
        {"$set": {"flgactive": False, "deletedAt": _now_iso()}},
    )
    return result.modified_count


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────


def _format_rel(rel: dict[str, Any]) -> str:
    rid = rel.get("_id") or rel.get("id") or "<no-id>"
    src = f"{rel.get('sourceTable')!r}.{rel.get('sourceColumn')!r}"
    tgt = f"{rel.get('targetTable')!r}.{rel.get('targetColumn')!r}"
    reason = rel.get("_dangling_reason") or {}
    flags = []
    if not reason.get("source_table_known"):
        flags.append("src-table-missing")
    elif not reason.get("source_column_resolves"):
        flags.append("src-col-unknown")
    if not reason.get("target_table_known"):
        flags.append("tgt-table-missing")
    elif not reason.get("target_column_resolves"):
        flags.append("tgt-col-unknown")
    flags_str = ",".join(flags) or "no-modelId"
    return f"  - {rid}  model={rel.get('modelId')!r}  {src} -> {tgt}   [{flags_str}]"


async def main(apply: bool) -> None:
    print(f"[prune] apply={apply}")
    print("[prune] connecting to MongoDB / Cosmos DB …")
    await motor_connect()
    try:
        db = await get_db()

        print("[prune] indexing model_tables …")
        table_index = await _build_table_index(db)
        print(f"[prune] table index entries (id+name): {len(table_index)}")

        print("[prune] scanning model_relationships …")
        candidates = await _scan_dangling(db, table_index)
        print(f"[prune] dangling relationships found: {len(candidates)}")

        if candidates:
            print("\n[prune] candidates:")
            for rel in candidates:
                print(_format_rel(rel))

        if not apply:
            print(
                "\n[prune] DRY RUN — nothing was written. "
                "Re-run with --apply to soft-delete the candidates."
            )
            return

        ids = [c["_id"] for c in candidates if "_id" in c]
        deleted = await _soft_delete(db, ids)
        print(f"\n[prune] soft-deleted {deleted} relationship doc(s).")
    finally:
        await motor_disconnect()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Soft-delete relationships whose source/target column refs "
            "don't resolve to any column on the corresponding table. "
            "Default behaviour is dry-run."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually mutate the DB (soft-delete). Without this flag the "
        "script only reports what it would do.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(main(apply=args.apply))
