"""Migración Erwin XML → colecciones publicadas de Data Model Hub.

SIN `--apply` es un dry-run: parsea, corre el gate de calidad y muestra el
plan (no abre conexión a la BD). Con `--apply` escribe en Mongo/Cosmos
(patrón "Erwin one-shot": directo a colecciones publicadas, sin changeset).

Idempotente: los ids de plataforma son deterministas (uuid5 del Long_Id de
Erwin), así que re-correr el mismo archivo (o una versión nueva del mismo
modelo) actualiza en su sitio. Documentos preexistentes con la misma clave
natural pero OTRO id (creados a mano en la plataforma) se respetan: el
estándar (glosario/dominios/defs UDP) se REUSA por clave natural y las
tablas/vistas en conflicto se OMITEN con aviso.

Jerarquía: Modelo → proyecto · Subject Area → folder · ER_Diagram → canvas.
Layout inicial en grilla; correr `scripts/arrange_all` después para ELK.

Uso:
  .venv/bin/python -m scripts.erwin_migration.migrate modelo.xml [más.xml ...]
      [--apply]              escribir (sin esto: dry-run)
      [--project "Nombre"]   override del nombre de proyecto (default: nombre del modelo)
      [--only-sa CPYBCAPYM]  migrar solo canvases/folders de esa subject area (piloto)
      [--force]              continuar aunque el gate tenga ERRORs
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

from . import erwin_parser as ep
from . import policies as pol
from .quality import analyze, summarize

_STAMP = {"migratedFrom": "erwin"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm_type(t: str) -> str:
    return re.sub(r"\s+", "", (t or "").upper())


class Migrator:
    def __init__(self, db, model: ep.ErwinModel, project_name: str | None,
                 only_sa: str | None):
        self.db = db
        self.m = model
        self.project_name = project_name or model.name or "Modelo Erwin"
        self.only_sa = only_sa
        self.schema_of = model.owner_schema()
        self.attr_idx = model.attr_index()
        self.stats: Counter = Counter()
        self.warnings: list[str] = []
        # mapas Erwin id → id de plataforma FINAL (puede ser un doc preexistente)
        self.domain_pid: dict[str, str] = {}
        self.udp_pid: dict[str, str] = {}      # clave colapsada → def id plataforma
        self.table_pid: dict[str, str] = {}
        self.col_pid: dict[str, str] = {}
        self.view_pid: dict[str, str] = {}
        self.collapsed = pol.collapse_udp_defs(model.udp_defs)
        self.udp_vals = pol.resolve_udp_values(model.udp_values, self.collapsed)

    # ---------- helpers ----------
    def _upsert(self, coll: str, pid: str, doc: dict) -> None:
        """$set idempotente por _id; createdAt/flgactive solo al insertar."""
        self.db[coll].update_one(
            {"_id": pid},
            {"$set": {**doc, **_STAMP, "updatedAt": _now()},
             "$setOnInsert": {"flgactive": True, "createdAt": _now()}},
            upsert=True)

    def _udp_values_for(self, erwin_id: str, level: str) -> dict[str, str]:
        out = {}
        for key, entry in self.collapsed.items():
            if entry["level"] != level or key not in self.udp_pid:
                continue
            val = self.udp_vals.get((erwin_id, key))
            if val:
                out[self.udp_pid[key]] = val
        return out

    # ---------- pasos ----------
    def standards(self) -> None:
        # dominios padre (solo custom; los builtin de Erwin no aportan)
        for d in self.m.domains.values():
            if d.builtin or d.name.startswith("<"):
                continue
            existing = self.db.parent_domains.find_one(
                {"name": re.compile(f"^{re.escape(d.name)}$", re.I),
                 "flgactive": {"$ne": False}}, {"_id": 1})
            pid = existing["_id"] if existing else pol.platform_id(d.id)
            self.domain_pid[d.id] = pid
            if existing:
                self.stats["dominios reusados"] += 1
                continue
            self._upsert("parent_domains", pid, {
                "name": d.name, "defaultDataType": d.data_type or "STRING",
                "namingTerm": None, "description": d.definition or None,
                "erwinLongId": d.id})
            self.stats["dominios creados"] += 1

        # glosario (term+abbrev; nunca tocar existentes/locked)
        seen_terms: set[str] = set()
        for g in self.m.glossary:
            term = g[0].strip()
            abbrev = g[1].strip() if len(g) > 1 else ""
            if not term or term.upper() in seen_terms:
                self.stats["glosario duplicado omitido"] += bool(term)
                continue
            seen_terms.add(term.upper())
            if self.db.glossary_terms.find_one(
                    {"term": re.compile(f"^{re.escape(term)}$", re.I),
                     "flgactive": {"$ne": False}}, {"_id": 1}):
                self.stats["glosario reusado"] += 1
                continue
            self._upsert("glossary_terms", pol.platform_id(f"gloss|{term.upper()}"), {
                "term": term, "abbrev": abbrev, "scope": "column",
                "wordType": None, "locked": False})
            self.stats["glosario creado"] += 1

        # defs UDP (lookup SIEMPRE por nombre + NIVEL — lección C8, doc 11 §4b)
        values_by_key: dict[str, set] = defaultdict(set)
        for (oid, key), val in self.udp_vals.items():
            values_by_key[key].add(val)
        for key, entry in self.collapsed.items():
            if not values_by_key.get(key):
                self.stats["defs UDP sin valores (omitidas)"] += 1
                continue
            existing = self.db.udp_definitions.find_one(
                {"name": re.compile(f"^{re.escape(entry['name'])}$", re.I),
                 "level": entry["level"], "flgactive": {"$ne": False}}, {"_id": 1})
            pid = existing["_id"] if existing else pol.platform_id(f"udp|{key}")
            self.udp_pid[key] = pid
            if existing:
                self.stats["defs UDP reusadas"] += 1
                continue
            allowed = (sorted(values_by_key[key] | ({entry["default"]} if entry["default"] else set()))
                       if entry["dataType"] == "list" else [])
            self._upsert("udp_definitions", pid, {
                "name": entry["name"], "level": entry["level"],
                "dataType": entry["dataType"], "defaultValue": entry["default"],
                "allowedValues": allowed,
                "description": "Migrada de Erwin"})
            self.stats["defs UDP creadas"] += 1

    def tables(self) -> None:
        taken: set[str] = set()
        for e in self.m.entities.values():
            schema = pol.schema_or_default(self.schema_of.get(e.id))
            nat_key = f"{schema}.{e.physical}".upper()
            pid = pol.platform_id(e.id)
            other = self.db.canonical_tables.find_one(
                {"schema": schema, "physicalName": e.physical,
                 "_id": {"$ne": pid}, "flgactive": {"$ne": False}}, {"_id": 1})
            if other or nat_key in taken:
                self.warnings.append(f"tabla {nat_key} ya existe con otro id → OMITIDA")
                self.stats["tablas omitidas (conflicto)"] += 1
                continue
            taken.add(nat_key)
            self.table_pid[e.id] = pid
            self._upsert("canonical_tables", pid, {
                "physicalName": e.physical, "logicalName": e.name,
                "schema": schema,
                "description": e.definition or e.comment or None,
                "udpValues": self._udp_values_for(e.id, "table"),
                "erwinLongId": e.id})
            self.stats["tablas"] += 1

            cols, dropped = pol.dedupe_columns(e.attributes)
            self.stats["columnas duplicadas descartadas"] += len(dropped)
            for i, a in enumerate(cols):
                cpid = pol.platform_id(a.id)
                self.col_pid[a.id] = cpid
                dom_pid = self.domain_pid.get(a.domain_ref or "")
                dom = self.m.domains.get(a.domain_ref or "")
                overridden = bool(dom_pid and dom
                                  and _norm_type(dom.data_type) != _norm_type(a.data_type))
                self._upsert("canonical_columns", cpid, {
                    "tableId": pid, "physicalName": a.physical,
                    "logicalName": a.name, "parentDomainId": dom_pid,
                    "dataType": a.data_type or "STRING",
                    "typeOverridden": overridden,
                    "isPrimaryKey": a.id in e.pk_attr_ids,
                    "isForeignKey": bool(a.parent_attr_ref),
                    "isNullable": a.nullable, "isPartition": False,
                    "description": a.definition or a.comment or None,
                    "ordinal": i,
                    "udpValues": self._udp_values_for(a.id, "column"),
                    "erwinLongId": a.id})
                self.stats["columnas"] += 1

    def relationships(self) -> None:
        pairs = self.m.fk_pairs()
        for r in self.m.relationships.values():
            if r.rel_type not in (ep.REL_IDENTIFYING, ep.REL_NON_IDENTIFYING):
                continue
            src_t = self.table_pid.get(r.child_ref)     # hijo = lado FK (source)
            tgt_t = self.table_pid.get(r.parent_ref)    # padre = lado PK (target)
            if not src_t or not tgt_t:
                self.stats["relaciones omitidas (tabla no migrada)"] += 1
                continue
            ok = 0
            for p_attr, c_attr in pairs.get(r.id, []):
                s_col, t_col = self.col_pid.get(c_attr), self.col_pid.get(p_attr)
                if not s_col or not t_col:
                    continue
                self._upsert("relationships", pol.platform_id(f"{r.id}|{c_attr}"), {
                    "sourceTableId": src_t, "sourceColumnId": s_col,
                    "targetTableId": tgt_t, "targetColumnId": t_col,
                    "sourceCardinality": "one",
                    "targetCardinality": pol.map_cardinality(r.cardinality),
                    "identifying": r.rel_type == ep.REL_IDENTIFYING,
                    "erwinLongId": r.id})
                ok += 1
            if ok:
                self.stats["relaciones"] += ok
            else:
                self.stats["relaciones omitidas (sin pares FK)"] += 1

    def views(self) -> None:
        view_rels = self.m.view_source_rels()
        for v in self.m.views.values():
            rels = [r for r in view_rels.get(v.id, []) if r.parent_ref in self.table_pid]
            source_pids = list(dict.fromkeys(self.table_pid[r.parent_ref] for r in rels))
            if not source_pids:
                self.stats["vistas omitidas (sin fuente)"] += 1
                continue
            schema = pol.schema_or_default(self.schema_of.get(v.id))
            pid = pol.platform_id(v.id)
            other = self.db.views.find_one(
                {"name": v.name, "schema": schema, "_id": {"$ne": pid},
                 "flgactive": {"$ne": False}}, {"_id": 1})
            if other:
                self.warnings.append(f"vista {schema}.{v.name} ya existe con otro id → OMITIDA")
                self.stats["vistas omitidas (conflicto)"] += 1
                continue
            self.view_pid[v.id] = pid

            cols, dropped = pol.dedupe_columns(v.attributes)
            self.stats["columnas duplicadas descartadas"] += len(dropped)
            sources = []
            for a in cols:
                origin = self.attr_idx.get(a.parent_attr_ref or "")
                origin_tid = (self.table_pid.get(origin.owner_id)
                              if origin and origin.owner_kind == "Entity" else None)
                cast = (a.data_type if origin
                        and _norm_type(origin.data_type) != _norm_type(a.data_type)
                        else None)
                sources.append({
                    "tableId": origin_tid or source_pids[0],
                    "column": origin.physical if origin else a.physical,
                    "outputAlias": a.physical,
                    **({"castType": cast} if cast else {}),
                })
            self._upsert("views", pid, {
                "name": v.name, "schema": schema, "sql": "",
                "description": v.definition or v.comment or None,
                "tableId": source_pids[0], "sourceTableIds": source_pids,
                "sources": sources, "tags": [], "filter": None,
                "outputAlias": None, "expression": None,
                # decisión owner: TODAS las vistas visibles en canvas
                "showOnCanvas": True, "joinOverride": None,
                "erwinLongId": v.id})
            self.stats["vistas"] += 1

    def canvases(self) -> None:
        # proyecto (reusar por nombre)
        existing = self.db.projects.find_one(
            {"name": self.project_name, "flgactive": {"$ne": False}}, {"_id": 1})
        proj_id = existing["_id"] if existing else pol.platform_id(
            f"project|{self.project_name}")
        if not existing:
            self._upsert("projects", proj_id, {
                "name": self.project_name,
                "description": f"Migrado de Erwin — {self.m.name}"})
            self.stats["proyectos creados"] += 1

        # UDP de nivel canvas definidos a nivel Model → a todos los canvases
        model_udp: dict[str, str] = {}
        for key, entry in self.collapsed.items():
            if entry["level"] != "canvas" or key not in self.udp_pid:
                continue
            for (oid, k), val in self.udp_vals.items():
                if k == key and val:
                    model_udp[self.udp_pid[key]] = val

        folder_pid: dict[str, str] = {}
        for i, sa in enumerate(sorted(self.m.subject_areas, key=lambda s: s["order"])):
            if self.only_sa and sa["name"] != self.only_sa:
                continue
            fid = pol.platform_id(f"folder|{sa['id']}")
            folder_pid[sa["name"]] = fid
            self._upsert("folders", fid, {
                "projectId": proj_id, "parentFolderId": None,
                "name": sa["name"], "order": i, "erwinLongId": sa["id"]})
            self.stats["folders"] += 1

        for d in self.m.diagrams:
            if self.only_sa and d.subject_area != self.only_sa:
                continue
            table_ids, layout = [], {}
            ordered = list(dict.fromkeys(ref for ref, _a in d.shapes))
            ents = [r for r in ordered if r in self.table_pid]
            vws = [r for r in ordered if r in self.view_pid]
            for n, ref in enumerate(ents + vws):
                pid = self.table_pid.get(ref) or self.view_pid[ref]
                layout[pid] = {"x": 40 + (n % 4) * 480, "y": 40 + (n // 4) * 360}
                if ref in self.table_pid:
                    table_ids.append(pid)
            cid = pol.platform_id(d.id)
            self._upsert("subject_areas", cid, {
                "projectId": proj_id,
                "folderId": folder_pid.get(d.subject_area),
                "name": d.name, "tableIds": table_ids, "layout": layout,
                "drawings": [], "udpValues": dict(model_udp),
                "erwinLongId": d.id})
            self.stats["canvases"] += 1

    def run(self) -> None:
        self.standards()
        self.tables()
        self.relationships()
        self.views()
        self.canvases()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Migración Erwin XML → Data Model Hub")
    ap.add_argument("xml", nargs="+")
    ap.add_argument("--apply", action="store_true",
                    help="escribir en la BD (sin esto: dry-run)")
    ap.add_argument("--project", help="nombre de proyecto destino")
    ap.add_argument("--only-sa", help="migrar solo canvases de esta subject area")
    ap.add_argument("--force", action="store_true",
                    help="continuar aunque el gate de calidad tenga ERRORs")
    args = ap.parse_args(argv)

    rc = 0
    for path in args.xml:
        print(f"\n{'=' * 72}\nARCHIVO: {path}")
        m = ep.parse(path)
        s = summarize(m)
        print(f"Modelo: {s['modelo']} — " +
              " · ".join(f"{k}={v}" for k, v in s.items() if k != "modelo"))

        errors = [x for x in analyze(m) if x["severity"] == "ERROR"]
        if errors:
            for x in errors:
                print(f"[ERROR] {x['code']} ({x['count']}): {x['samples'][:3]}")
            if not args.force:
                print("Gate de calidad con ERRORs — corre primero "
                      "`python -m scripts.erwin_migration.quality` y decide; "
                      "o repite con --force para omitir esos objetos.")
                rc = 1
                continue

        if not args.apply:
            print("\nDRY-RUN (sin --apply no se escribe nada). Plan:")
            collapsed = pol.collapse_udp_defs(m.udp_defs)
            print(f"  proyecto: {args.project or m.name}")
            print(f"  folders={len(m.subject_areas)} canvases={len(m.diagrams)} "
                  f"tablas={len(m.entities)} vistas={len(m.views)} "
                  f"defsUDP={len(collapsed)} glosario={len(m.glossary)} "
                  f"dominios={sum(1 for d in m.domains.values() if not d.builtin)}")
            continue

        from dotenv import load_dotenv
        from pymongo import MongoClient
        load_dotenv()
        db = MongoClient(os.environ["COSMOS_CONNECTION_STRING"])[
            os.environ.get("COSMOS_DATABASE", "db_modeler")]
        mig = Migrator(db, m, args.project, args.only_sa)
        mig.run()
        print("\nRESULTADO:")
        for k, v in sorted(mig.stats.items()):
            print(f"  {k}: {v}")
        for w in mig.warnings[:20]:
            print(f"  ⚠ {w}")
        if len(mig.warnings) > 20:
            print(f"  ⚠ … y {len(mig.warnings) - 20} avisos más")
        print("\nSiguientes pasos: `python -m scripts.audit_data_consistency` "
              "(validación) y `scripts/arrange_all` (layout ELK).")
    return rc


if __name__ == "__main__":
    sys.exit(main())
