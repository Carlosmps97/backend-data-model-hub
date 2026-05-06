"""One-shot migration — stamp `TableColumn.id` on every persisted column.

WHY
    Before the column-id migration, relationships referenced their source
    and target columns by *name*. The frontend React Flow handles were
    keyed by name too. That meant the moment a user renamed a column, the
    rel kept pointing at the old name and the canvas edge silently
    orphaned (warning: "Couldn't create edge for source handle id: …").

    After the migration, every `TableColumn` carries a stable `id`
    (UUIDv4) that is used everywhere the column is referenced:
    `Relationship.sourceColumn` / `targetColumn` and React Flow handle
    IDs. Columns authored going forward get their id from the TableForm
    editor, the DBML/SQL/Excel importers, the agent mapper, and the
    backend safety net in `models_db._ensure_column_ids`.

    This script takes care of the *legacy* data — tables already sitting
    in Cosmos DB from before the migration whose `columns[*].id` field
    is missing.

WHAT IT DOES
    1. Walks every document in `model_tables` (filtered by `flgactive ≠ false`).
    2. For each column without an `id`, assigns a fresh UUIDv4.
    3. Re-saves the document with the patched `columns` array and a
       refreshed `updatedAt`.
    4. Purges **all** documents from `model_relationships` (hard delete,
       not soft delete), because legacy relationships still hold column
       *names* in `sourceColumn` / `targetColumn` and there is no safe
       way to reattach them to the newly-minted ids without risking
       false matches on name collisions. The user explicitly chose this
       trade-off (they have few relationships, will redraw them by
       hand).
    5. Reports a per-collection summary at the end.

SAFETY
    - Idempotent: running it twice only affects docs whose columns are
      still missing an id. The second run will report 0 backfills.
    - Does NOT touch `models`, `model_views`, `projects`, `users`, or
      the agent's `column_catalog` collection.
    - Prints a dry-run summary when invoked with `--dry-run`. Without
      that flag, mutations are applied immediately.

USAGE
    From the `agent-modeler/` repo root with the virtualenv active:

        python -m scripts.backfill_column_ids --dry-run   # preview
        python -m scripts.backfill_column_ids             # apply

    The script reads `COSMOS_CONNECTION_STRING` and `COSMOS_DATABASE`
    from the environment (same vars the FastAPI backend uses).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# Ensure `src.*` is importable when invoked as a module.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.db.motor_client import connect as motor_connect, disconnect as motor_disconnect, get_db  # noqa: E402


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def backfill_column_ids(dry_run: bool) -> dict[str, int]:
    """Walk model_tables and assign an id to every column missing one.

    Returns a dict of counters suitable for summary output.
    """
    db = await get_db()
    coll = db["model_tables"]

    total_docs = 0
    docs_touched = 0
    cols_backfilled = 0

    cursor = coll.find({"flgactive": {"$ne": False}})
    async for doc in cursor:
        total_docs += 1
        columns = doc.get("columns") or []
        if not isinstance(columns, list):
            continue

        mutated = False
        for col in columns:
            if not isinstance(col, dict):
                continue
            cid = col.get("id")
            if not isinstance(cid, str) or not cid:
                col["id"] = str(uuid.uuid4())
                cols_backfilled += 1
                mutated = True

        if not mutated:
            continue

        docs_touched += 1
        if dry_run:
            continue

        await coll.update_one(
            {"_id": doc["_id"]},
            {"$set": {"columns": columns, "updatedAt": _now_iso()}},
        )

    return {
        "tables_scanned": total_docs,
        "tables_updated": docs_touched,
        "columns_backfilled": cols_backfilled,
    }


async def purge_relationships(dry_run: bool) -> dict[str, int]:
    """Hard-delete every doc in `model_relationships`.

    We do NOT attempt to remap existing relationships to the newly
    assigned column ids — the mapping would be based on the old
    `sourceColumn` / `targetColumn` string which is a *name*, and with
    duplicate column names possible across tables the guess could easily
    wire the wrong handles together. The user will recreate the few
    relationships they had.
    """
    db = await get_db()
    coll = db["model_relationships"]

    # Count first for the summary, regardless of --dry-run.
    total = await coll.count_documents({})
    if dry_run or total == 0:
        return {"relationships_scanned": total, "relationships_deleted": 0}

    result = await coll.delete_many({})
    return {
        "relationships_scanned": total,
        "relationships_deleted": result.deleted_count,
    }


async def main(dry_run: bool) -> None:
    print(f"[migration] dry_run={dry_run}")
    print("[migration] connecting to MongoDB / Cosmos DB …")
    await motor_connect()
    try:
        print("[migration] backfilling column ids …")
        col_stats = await backfill_column_ids(dry_run)
        print(f"[migration] tables scanned   : {col_stats['tables_scanned']}")
        print(f"[migration] tables updated   : {col_stats['tables_updated']}")
        print(f"[migration] columns patched  : {col_stats['columns_backfilled']}")

        print("[migration] purging stale relationships …")
        rel_stats = await purge_relationships(dry_run)
        print(f"[migration] rels scanned     : {rel_stats['relationships_scanned']}")
        print(f"[migration] rels deleted     : {rel_stats['relationships_deleted']}")

        if dry_run:
            print(
                "\n[migration] DRY RUN — nothing was written. "
                "Re-run without --dry-run to apply."
            )
        else:
            print("\n[migration] done.")
    finally:
        await motor_disconnect()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Backfill `TableColumn.id` on existing model_tables docs and purge "
            "legacy relationships that still reference columns by name."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Don't write anything — only report what would change. "
            "Useful to sanity-check the target database before applying."
        ),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(main(dry_run=args.dry_run))
