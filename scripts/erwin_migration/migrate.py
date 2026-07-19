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
        self.schemas_used: set[str] = set()    # nombres de schema de tablas/vistas migradas
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

        # defs UDP (lookup SIEMPRE por nombre + NIVEL — lección C8, doc 11 §4b).
        # Se cargan COMPLETAS: TODAS las defs y sus valores permitidos de la
        # LISTA EXPLÍCITA del XML (tag_Udp_Values_List), AUNQUE no se usen en
        # ninguna tabla de este modelo — son estándares compartidos que se
        # repiten entre XML y pueden usarse en otras tablas (pedido owner
        # 2026-07-18). El uso observado y el default se agregan por si algún
        # valor no estuviera en la lista explícita.
        values_by_key: dict[str, set] = defaultdict(set)
        for (oid, key), val in self.udp_vals.items():
            values_by_key[key].add(val)
        for key, entry in self.collapsed.items():
            # texto SIN uso: nada que cargar (no tiene enum). Las LISTAS se
            # cargan SIEMPRE — traen su enum explícito aunque no se use acá.
            if entry["dataType"] != "list" and not values_by_key.get(key):
                self.stats["defs UDP de texto sin uso (omitidas)"] += 1
                continue
            allowed: list[str] = []
            if entry["dataType"] == "list":
                allowed = list(entry.get("allowed_values") or [])
                extra = values_by_key.get(key, set()) | (
                    {entry["default"]} if entry["default"] else set())
                for v in sorted(extra):
                    if v and v not in allowed:
                        allowed.append(v)
            existing = self.db.udp_definitions.find_one(
                {"name": re.compile(f"^{re.escape(entry['name'])}$", re.I),
                 "level": entry["level"], "flgactive": {"$ne": False}},
                {"_id": 1, "allowedValues": 1})
            pid = existing["_id"] if existing else pol.platform_id(f"udp|{key}")
            self.udp_pid[key] = pid
            if existing:
                # converge la lista entre XML: agrega los valores que falten
                missing = [v for v in allowed
                           if v not in (existing.get("allowedValues") or [])]
                if missing:
                    self._upsert("udp_definitions", pid, {
                        "allowedValues": (existing.get("allowedValues") or []) + missing})
                    self.stats["defs UDP con valores agregados"] += 1
                self.stats["defs UDP reusadas"] += 1
                continue
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
            self.schemas_used.add(schema)
            self._upsert("canonical_tables", pid, {
                "physicalName": e.physical, "logicalName": e.name,
                "schema": schema,
                "description": e.definition or e.comment or None,
                "udpValues": self._udp_values_for(e.id, "table"),
                "erwinLongId": e.id})
            self.stats["tablas"] += 1

            cols, dropped = pol.dedupe_columns(e.attributes)
            self.stats["columnas duplicadas descartadas"] += len(dropped)
            pk_pos = {aid: i for i, aid in enumerate(e.pk_attr_order)}
            current_cpids = {pol.platform_id(a.id) for a in cols}
            # Partición nativa desde el UDP "Particion" (PART_nn): sólo si el
            # correlativo es congruente con el orden físico (política 07-17).
            part_vals = [(a.id, corr) for a in cols
                         if (corr := pol.partition_correlative(
                             self.udp_vals.get((a.id, pol.PARTITION_UDP_KEY)))) is not None]
            part_ids, part_skip = pol.partition_marks(part_vals)
            if part_skip:
                self.warnings.append(f"partición NO marcada en {nat_key}: {part_skip}")
                self.stats["tablas con partición incongruente (sin marcar)"] += 1
            elif part_ids:
                self.stats["tablas con partición nativa"] += 1
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
                    "pkPosition": pk_pos.get(a.id),
                    "isForeignKey": bool(a.parent_attr_ref),
                    "isNullable": a.nullable, "isPartition": a.id in part_ids,
                    "description": a.definition or a.comment or None,
                    "ordinal": i,
                    "udpValues": self._udp_values_for(a.id, "column"),
                    "erwinLongId": a.id})
                self.stats["columnas"] += 1

            # Re-runs auto-saneados: columnas MIGRADAS de esta tabla que ya no
            # vienen en el archivo (duplicado que ahora se dedup-ea distinto,
            # o atributo eliminado en una versión nueva del XML) → soft-delete.
            # SOLO docs con erwinLongId — lo creado a mano en la plataforma
            # jamás se toca.
            stale = self.db.canonical_columns.find(
                {"tableId": pid, "flgactive": {"$ne": False},
                 "erwinLongId": {"$exists": True}, "_id": {"$nin": list(current_cpids)}},
                {"_id": 1})
            for d in stale:
                self.db.canonical_columns.update_one(
                    {"_id": d["_id"]},
                    {"$set": {"flgactive": False, "deletedAt": _now(),
                              "updatedAt": _now()}})
                self.stats["columnas migradas retiradas (ya no están en el XML)"] += 1

    def relationships(self) -> None:
        """v2 (doc 19): UN doc por relación Erwin, con TODOS sus pares de
        columnas (las FK compuestas dejan de partirse en N docs). El id de
        plataforma es determinista por relación (`platform_id(r.id)`)."""
        pairs = self.m.fk_pairs()
        for r in self.m.relationships.values():
            if r.rel_type not in (ep.REL_IDENTIFYING, ep.REL_NON_IDENTIFYING):
                continue
            child_t = self.table_pid.get(r.child_ref)    # hijo = lado FK
            parent_t = self.table_pid.get(r.parent_ref)  # padre = lado PK
            if not child_t or not parent_t:
                self.stats["relaciones omitidas (tabla no migrada)"] += 1
                continue
            rel_pairs = []
            for p_attr, c_attr in pairs.get(r.id, []):
                p_col, c_col = self.col_pid.get(p_attr), self.col_pid.get(c_attr)
                if p_col and c_col:
                    rel_pairs.append({"parentColumnId": p_col,
                                      "childColumnId": c_col, "roleName": None})
            if not rel_pairs:
                self.stats["relaciones omitidas (sin pares FK)"] += 1
                continue
            self._upsert("relationships", pol.platform_id(r.id), {
                "parentTableId": parent_t, "childTableId": child_t,
                "pairs": rel_pairs,
                # Lado padre desde Null_Option_Type ("100" = FK nullable →
                # padre opcional 0..1), como el rombo del diagrama Erwin.
                "parentCardinality": pol.map_parent_cardinality(r.null_option),
                "childCardinality": pol.map_cardinality(r.cardinality),
                "identifying": r.rel_type == ep.REL_IDENTIFYING,
                "erwinLongId": r.id})
            self.stats["relaciones"] += 1
            if len(rel_pairs) > 1:
                self.stats["relaciones compuestas (2+ pares)"] += 1

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
            self.schemas_used.add(schema)

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
                src = {
                    "tableId": origin_tid or source_pids[0],
                    "column": origin.physical if origin else a.physical,
                    "outputAlias": a.physical,
                }
                if cast:
                    src["castType"] = cast
                # F5: definición PROPIA de la columna-de-vista (Erwin la trae a
                # nivel de columna-de-vista). Se guarda SOLO si APORTA: difiere de
                # la del origen físico, o el origen no tiene. Las passthrough
                # idénticas NO se duplican (heredan de la física en el front).
                vdef = (a.definition or a.comment or "").strip()
                odef = ((origin.definition or origin.comment or "").strip()
                        if origin else "")
                if vdef and vdef != odef:
                    src["description"] = vdef
                    self.stats["columnas de vista con definición propia"] += 1
                sources.append(src)
            self._upsert("views", pid, {
                # `sql` = el CREATE VIEW ORIGINAL de Erwin (ViewProps.SQL, doc 19
                # §12). El export DDL sigue generando desde `sources` (estructura);
                # esto preserva la definición fuente como metadata de referencia.
                "name": v.name, "schema": schema, "sql": v.sql or "",
                "description": v.definition or v.comment or None,
                "tableId": source_pids[0], "sourceTableIds": source_pids,
                "sources": sources, "tags": [], "filter": None,
                "outputAlias": None, "expression": None,
                # decisión owner: TODAS las vistas visibles en canvas
                "showOnCanvas": True, "joinOverride": None,
                "erwinLongId": v.id})
            self.stats["vistas"] += 1

    def schema_docs(self) -> None:
        """Entidad `schemas` (doc 18): un doc por nombre de schema usado por
        las tablas/vistas migradas. Reusa por nombre (case-insensitive); ids
        `sch-<name>` (misma convención que scripts/backfill_schemas.py)."""
        for name in sorted(self.schemas_used):
            existing = self.db.schemas.find_one(
                {"name": re.compile(f"^{re.escape(name)}$", re.I),
                 "flgactive": {"$ne": False}}, {"_id": 1})
            if existing:
                self.stats["schemas reusados"] += 1
                continue
            self._upsert("schemas", f"sch-{name}", {"name": name, "description": None})
            self.stats["schemas creados"] += 1

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
            # Re-runs NO pisan el layout ya trabajado (ELK/arreglos a mano):
            # los nodos existentes conservan su posición; la grilla default
            # solo aplica a nodos NUEVOS de esta corrida.
            prev = self.db.subject_areas.find_one({"_id": cid}, {"layout": 1})
            if prev and prev.get("layout"):
                layout = {k: prev["layout"].get(k, v) for k, v in layout.items()}
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
        self.schema_docs()
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
