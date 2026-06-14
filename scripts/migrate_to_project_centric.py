"""Migrate Cosmos `db_modeler` from model-centric → project-centric (Aurora).

Transforms the legacy schema (collections `models` / `model_tables` /
`model_relationships` / `model_views`, sharded by `modelId`) into the new
project-centric schema:

- Each project's models become **layers** (ModelLevels) embedded in the project.
- Each model's `domainCatalog` becomes project-level **domains** (vertical).
- Tables move to `project_tables` (shard key `projectId`), tagged with
  `layer`/`domain`/`subdomain`, with their `model_views` embedded as SQL `views[]`.
- Relationships move to `project_relationships` with the nested
  `source/target/cardinality` shape.
- Seeds the **Lakehouse** demo project (see `seed_lakehouse.py`).
- Purges all soft-deleted docs and drops the legacy collections.

Idempotent: a project that already has `project_tables` is skipped; the Lakehouse
seed is skipped if it already exists. Safe to re-run.

Usage (from repo root, backend venv):
    python -m scripts.migrate_to_project_centric --dry-run     # preview only
    python -m scripts.migrate_to_project_centric               # apply (backs up first)
    python -m scripts.migrate_to_project_centric --no-seed --no-purge
"""

from __future__ import annotations

import argparse
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

import os
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.seed_lakehouse import build_lakehouse  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

LEGACY_COLLECTIONS = ["models", "model_tables", "model_relationships", "model_views"]
NEW_CHILD_COLLECTIONS = ["project_tables", "project_relationships"]
LAYER_PALETTE = ["#0e7490", "#4f46e5", "#7c3aed", "#0d9488", "#b45309", "#dc2626", "#0891b2"]
DEFAULT_COLOR = "#4f46e5"
CARD_MAP = {"one-to-one": "1:1", "one-to-many": "1:N", "many-to-many": "N:N"}
ACTIVE = {"flgactive": {"$ne": False}}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(text: str, used: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "modelo"
    slug, n = base, 2
    while slug in used:
        slug = f"{base}-{n}"
        n += 1
    used.add(slug)
    return slug


def _ensure_col_ids(columns: list[dict]) -> list[dict]:
    for c in columns or []:
        if not isinstance(c.get("id"), str) or not c["id"]:
            c["id"] = str(uuid.uuid4())
    return columns or []


def _resolve_table(ref: str, ids: set[str], name_to_id: dict[str, str]) -> str | None:
    if ref in ids:
        return ref
    return name_to_id.get(ref)


def _resolve_col(columns: list[dict], ref: str) -> str:
    for c in columns:
        if c.get("id") == ref:
            return ref
    for c in columns:
        if c.get("name") == ref:
            return c["id"]
    return ref  # best effort — keep as-is


def _convert_view(v: dict) -> dict:
    vtype = "technical" if v.get("viewType") == "technical" else "business"
    if v.get("useCustomSql") and v.get("customSql"):
        select, where = v["customSql"], ""
    else:
        lines = []
        for c in v.get("columns") or []:
            if not c.get("include", True):
                continue
            base = c.get("expression") or c.get("sourceColumn") or ""
            alias = c.get("alias")
            lines.append(f"{base} AS {alias}" if alias else base)
        select = "\n".join(lines)
        where = v.get("whereClause") or ""
    return {
        "id": str(v.get("_id") or uuid.uuid4()), "name": v.get("name") or "vw",
        "type": vtype, "role": v.get("role") or "", "select": select, "where": where,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Migrate to project-centric schema")
    ap.add_argument("--dry-run", action="store_true", help="preview only, no writes (backup still taken)")
    ap.add_argument("--no-seed", action="store_true", help="skip Lakehouse demo seed")
    ap.add_argument("--no-purge", action="store_true", help="keep soft-deleted docs + legacy collections")
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

    # ── 1. Backup (always) ──────────────────────────────────────────────────
    backup: dict[str, list] = {}
    for c in ["projects", "users", *LEGACY_COLLECTIONS, *NEW_CHILD_COLLECTIONS]:
        if c in existing:
            backup[c] = list(db[c].find({}))
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = ROOT / "scripts" / f"_backup_{ts}.json"
    backup_path.write_text(json.dumps(backup, default=str, ensure_ascii=False, indent=1))
    print(f"  backup → {backup_path.name} ({sum(len(v) for v in backup.values())} docs)")

    # ── 2. Migrate each project ─────────────────────────────────────────────
    migrated_tables = migrated_rels = migrated_projects = 0
    for proj in db["projects"].find(ACTIVE):
        pid = str(proj["_id"])
        if db["project_tables"].count_documents({"projectId": pid, **ACTIVE}) > 0:
            print(f"  project '{proj.get('name')}' already migrated — skip")
            continue
        models = list(db["models"].find({"projectId": pid, **ACTIVE})) if "models" in existing else []
        if not models:
            print(f"  project '{proj.get('name')}' has no models — set empty layers/domains")

        used_levels: set[str] = set()
        engines: set[str] = set()
        layers: list[dict] = []
        domains: dict[str, dict] = {}
        p_tables: list[dict] = []
        p_rels: list[dict] = []

        for i, model in enumerate(models):
            mid = str(model["_id"])
            level_id = slugify(model.get("name") or f"modelo-{i}", used_levels)
            layers.append({
                "id": level_id, "name": model.get("name") or level_id,
                "full": model.get("description") or model.get("name") or level_id,
                "color": LAYER_PALETTE[i % len(LAYER_PALETTE)], "order": i,
                "engine": model.get("engine"), "desc": model.get("description"),
            })
            if model.get("engine"):
                engines.add(model["engine"])
            for dom in model.get("domainCatalog") or []:
                did = str(dom.get("id") or uuid.uuid4())
                if did not in domains:
                    domains[did] = {
                        "id": did, "name": dom.get("name") or did, "color": dom.get("color") or DEFAULT_COLOR,
                        "owner": "", "steward": "", "sensitivity": "Internal",
                        "description": dom.get("description") or "",
                        "subdomains": [sd.get("name") for sd in dom.get("subdomains") or [] if sd.get("name")],
                        "layers": [],
                    }
                if level_id not in domains[did]["layers"]:
                    domains[did]["layers"].append(level_id)

            def _dom_name_to_id(name: str | None) -> str:
                if not name:
                    return ""
                for d in domains.values():
                    if d["name"] == name:
                        return d["id"]
                return ""

            tdocs = list(db["model_tables"].find({"modelId": mid, **ACTIVE})) if "model_tables" in existing else []
            tids = {str(t["_id"]) for t in tdocs}
            name_to_id = {t.get("name"): str(t["_id"]) for t in tdocs}
            vdocs = list(db["model_views"].find({"modelId": mid, **ACTIVE})) if "model_views" in existing else []
            views_by_table: dict[str, list] = {}
            for v in vdocs:
                views_by_table.setdefault(str(v.get("sourceTableId")), []).append(_convert_view(v))

            for t in tdocs:
                tid = str(t["_id"])
                dom_id = t.get("domainId") or _dom_name_to_id(t.get("domain"))
                color = (domains.get(dom_id, {}).get("color")) or t.get("color") or DEFAULT_COLOR
                p_tables.append({
                    "_id": tid, "projectId": pid, "schema": t.get("schema"), "name": t.get("name"),
                    "logicalName": t.get("logicalName"), "layer": level_id, "domain": dom_id or "",
                    "subdomain": t.get("subdomain") or "", "color": color,
                    "functionalDefinition": t.get("functionalDefinition"),
                    "applicationCode": t.get("applicationCode"),
                    "columns": _ensure_col_ids(t.get("columns") or []),
                    "views": views_by_table.get(tid, []),
                    "tags": t.get("tags"), "partition": t.get("partition"),
                    "flgactive": True, "updatedAt": _now(),
                })

            for r in db["model_relationships"].find({"modelId": mid, **ACTIVE}) if "model_relationships" in existing else []:
                s_tid = _resolve_table(str(r.get("sourceTable")), tids, name_to_id)
                t_tid = _resolve_table(str(r.get("targetTable")), tids, name_to_id)
                if not s_tid or not t_tid:
                    print(f"    drop dangling rel {r.get('_id')} ({r.get('sourceTable')}→{r.get('targetTable')})")
                    continue
                s_cols = next((t.get("columns") or [] for t in tdocs if str(t["_id"]) == s_tid), [])
                t_cols = next((t.get("columns") or [] for t in tdocs if str(t["_id"]) == t_tid), [])
                p_rels.append({
                    "_id": str(r["_id"]), "projectId": pid,
                    "source": {"table": s_tid, "column": _resolve_col(s_cols, str(r.get("sourceColumn")))},
                    "target": {"table": t_tid, "column": _resolve_col(t_cols, str(r.get("targetColumn")))},
                    "cardinality": CARD_MAP.get(r.get("type"), "1:N"),
                    "flgactive": True, "updatedAt": _now(),
                })

        print(f"  project '{proj.get('name')}': {len(layers)} layers, {len(domains)} domains, "
              f"{len(p_tables)} tables, {len(p_rels)} rels")
        if commit:
            if p_tables:
                db["project_tables"].insert_many(p_tables)
            if p_rels:
                db["project_relationships"].insert_many(p_rels)
            db["projects"].update_one({"_id": proj["_id"]}, {"$set": {
                "engines": sorted(engines), "layers": layers, "domains": list(domains.values()),
                "updatedAt": _now(),
            }})
        migrated_projects += 1
        migrated_tables += len(p_tables)
        migrated_rels += len(p_rels)

    # ── 3. Seed Lakehouse ───────────────────────────────────────────────────
    if not args.no_seed:
        lk_project, lk_tables, lk_rels = build_lakehouse()
        if db["projects"].count_documents({"_id": lk_project["id"]}) > 0:
            print("  Lakehouse already exists — skip seed")
        else:
            print(f"  seed Lakehouse: 3 layers, 3 domains, {len(lk_tables)} tables, {len(lk_rels)} rels")
            if commit:
                now = _now()
                db["projects"].insert_one({
                    "_id": lk_project["id"], "name": lk_project["name"],
                    "description": lk_project["description"], "engines": lk_project["engines"],
                    "layers": lk_project["layers"], "domains": lk_project["domains"],
                    "createdAt": now, "updatedAt": now, "flgactive": True,
                })
                db["project_tables"].insert_many([
                    {"_id": t["id"], "projectId": lk_project["id"],
                     **{k: v for k, v in t.items() if k != "id"},
                     "flgactive": True, "updatedAt": now}
                    for t in lk_tables
                ])
                if lk_rels:
                    db["project_relationships"].insert_many([
                        {"_id": r["id"], "projectId": lk_project["id"],
                         **{k: v for k, v in r.items() if k != "id"},
                         "flgactive": True, "updatedAt": now}
                        for r in lk_rels
                    ])

    # ── 4. Purge soft-deleted + drop legacy collections ─────────────────────
    if not args.no_purge:
        purged = 0
        for c in ["projects", "users", *NEW_CHILD_COLLECTIONS]:
            if c in db.list_collection_names():
                n = db[c].count_documents({"flgactive": False})
                purged += n
                if commit and n:
                    db[c].delete_many({"flgactive": False})
        print(f"  purge soft-deleted: {purged} docs")
        for c in LEGACY_COLLECTIONS:
            if c in db.list_collection_names():
                print(f"  drop legacy collection: {c}")
                if commit:
                    db[c].drop()

    print(f"\nSUMMARY: projects={migrated_projects} tables={migrated_tables} rels={migrated_rels} "
          f"{'(DRY-RUN, nothing written)' if args.dry_run else '(APPLIED)'}")
    cli.close()


if __name__ == "__main__":
    main()
