"""Migrate project ids from auto-generated UUIDs → human-readable slugs.

Projects created through the API used `uuid4()` as their `_id`, so their URLs
look like `/projects/42211450-3635-4770-8b55-74fa60bcbdf1/editor`. Seeded
projects (e.g. Lakehouse) use a clean slug (`/projects/lakehouse/...`). This
script brings everything in sync by renaming each UUID-id project to a slug
derived from its name and re-pointing every reference:

  - `projects._id`                       (the project itself)
  - `project_tables.projectId`           (shard field)
  - `project_relationships.projectId`    (shard field)
  - `users.permissions[].projectId`      (per-project grants)

`column_catalog` is owned by the agent (`app-agents-modeler`) and is left
untouched on purpose.

Child docs are moved with delete+reinsert (preserving their `_id`) so the rename
is safe whether or not `projectId` is a shard key.

Idempotent: a project whose `_id` is already a slug (not UUID-shaped) is skipped.
Backs up the affected collections to `scripts/_backup_slugs_<ts>.json` first.

Usage (from repo root, backend venv):
    python -m scripts.migrate_project_ids_to_slugs --dry-run   # preview only
    python -m scripts.migrate_project_ids_to_slugs             # apply (backs up first)
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

CHILD_COLLECTIONS = ["project_tables", "project_relationships"]
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(text: str, used: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "project"
    slug, n = base, 2
    while slug in used:
        slug = f"{base}-{n}"
        n += 1
    used.add(slug)
    return slug


def main() -> None:
    ap = argparse.ArgumentParser(description="Rename project UUID ids → slugs")
    ap.add_argument("--dry-run", action="store_true", help="preview only, no writes (backup still taken)")
    args = ap.parse_args()
    commit = not args.dry_run

    conn = os.getenv("COSMOS_CONNECTION_STRING", "")
    dbname = os.getenv("COSMOS_DATABASE", "db_modeler")
    if not conn:
        raise SystemExit("COSMOS_CONNECTION_STRING not set")

    cli = MongoClient(conn, serverSelectionTimeoutMS=20000)
    db = cli[dbname]
    existing = set(db.list_collection_names())
    print(f"DB: {dbname}  mode: {'DRY-RUN' if args.dry_run else 'APPLY'}")

    # ── Backup (always) ──────────────────────────────────────────────────────
    backup: dict[str, list] = {}
    for c in ["projects", "users", *CHILD_COLLECTIONS]:
        if c in existing:
            backup[c] = list(db[c].find({}))
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = ROOT / "scripts" / f"_backup_slugs_{ts}.json"
    backup_path.write_text(json.dumps(backup, default=str, ensure_ascii=False, indent=1))
    print(f"  backup → {backup_path.name} ({sum(len(v) for v in backup.values())} docs)")

    # Reserve every current project id (active + soft-deleted) to avoid collisions.
    all_projects = list(db["projects"].find({}))
    used: set[str] = {str(p["_id"]) for p in all_projects}

    targets = [p for p in all_projects if UUID_RE.match(str(p["_id"]))]
    if not targets:
        print("  no UUID-id projects found — nothing to migrate. ✓")
        return

    migrated = 0
    for proj in targets:
        old_id = str(proj["_id"])
        # The project keeps its own id reserved; free it so the slug can reuse the base.
        used.discard(old_id)
        new_id = slugify(proj.get("name", ""), used)
        name = proj.get("name", "?")
        active = proj.get("flgactive", True) is not False

        n_tables = db["project_tables"].count_documents({"projectId": old_id})
        n_rels = db["project_relationships"].count_documents({"projectId": old_id})
        n_perms = db["users"].count_documents({"permissions.projectId": old_id})
        flag = "" if active else "  (soft-deleted)"
        print(f"\n  · '{name}'{flag}\n      {old_id}\n      → {new_id}")
        print(f"      tables={n_tables}  relationships={n_rels}  users-with-grant={n_perms}")

        if not commit:
            continue

        # 1) Recreate the project doc under the new _id.
        new_doc = dict(proj)
        new_doc["_id"] = new_id
        new_doc["updatedAt"] = _now()
        db["projects"].insert_one(new_doc)

        # 2) Move child docs (delete + reinsert preserves their _id; shard-safe).
        for col in CHILD_COLLECTIONS:
            docs = list(db[col].find({"projectId": old_id}))
            if docs:
                for d in docs:
                    d["projectId"] = new_id
                db[col].delete_many({"projectId": old_id})
                db[col].insert_many(docs)

        # 3) Re-point per-project permission grants on users.
        for u in db["users"].find({"permissions.projectId": old_id}):
            perms = u.get("permissions") or []
            for p in perms:
                if p.get("projectId") == old_id:
                    p["projectId"] = new_id
            db["users"].update_one({"_id": u["_id"]}, {"$set": {"permissions": perms}})

        # 4) Remove the old project doc.
        db["projects"].delete_one({"_id": old_id})
        migrated += 1

    if commit:
        print(f"\n  migrated {migrated} project(s). ✓")
    else:
        print(f"\n  DRY-RUN: would migrate {len(targets)} project(s). Re-run without --dry-run to apply.")


if __name__ == "__main__":
    main()
