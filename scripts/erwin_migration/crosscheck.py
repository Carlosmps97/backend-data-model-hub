"""Cross-check PRE-carga (gate 2, doc 32b §2): XML(s) contra la BD viva y
entre sí. Solo lectura — complementa a `quality` (que audita cada archivo en
frío) con TODO lo que depende del destino: solapes de tablas/vistas/schemas
con veredicto de la política multi-archivo (adoptar/actualizar por score),
diferencias de estándares (glosario/dominios/enums UDP con la convergencia
case-insensitive A3), defs UDP que se saltarían (A4), particiones a reasignar
(R6), duplicados internos con su ganadora (R3) y estimación de la carga.

Correr SIEMPRE antes de `migrate --apply` en cargas incrementales.

Uso:
  .venv/bin/python -m scripts.erwin_migration.crosscheck a.xml [b.xml ...]
      [--json salida.json]   volcado completo para automatizar
      [--no-db]              solo análisis de archivo(s) (sin conexión)
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter, defaultdict

from . import erwin_parser as ep
from . import policies as pol
from .quality import summarize

_ACTIVE = {"flgactive": {"$ne": False}}
_BATCH = 1000  # mismo lote que migrate (estimación)


def _nat(schema: str | None, name: str) -> str:
    return f"{pol.schema_or_default(schema)}.{name}".upper()


def _col_sig(e: ep.ErwinEntity) -> list[tuple]:
    cols, _ = pol.dedupe_columns(e.attributes)
    return sorted((a.physical.upper(),
                   (a.data_type or "").upper().replace(" ", ""),
                   a.nullable, a.id in e.pk_attr_ids) for a in cols)


class FileAnalysis:
    """Análisis por archivo (sin BD): usos, duplicados internos, particiones."""

    def __init__(self, path: str):
        self.path = path
        self.m = ep.parse(path)
        m = self.m
        self.schema_of = m.owner_schema()
        self.collapsed = pol.collapse_udp_defs(m.udp_defs)
        self.udp_vals = pol.resolve_udp_values(m.udp_values, self.collapsed)
        self.x_rels: Counter = Counter()
        for r in m.relationships.values():
            if r.rel_type in (ep.REL_IDENTIFYING, ep.REL_NON_IDENTIFYING,
                              ep.REL_SUBTYPE):
                self.x_rels[r.parent_ref] += 1
                self.x_rels[r.child_ref] += 1
        self.x_canv: Counter = Counter()
        for d in m.diagrams:
            for ref in {ref for ref, _a in d.shapes}:
                self.x_canv[ref] += 1
        self.x_views: Counter = Counter()
        for rels in m.view_source_rels().values():
            for parent in {r.parent_ref for r in rels}:
                self.x_views[parent] += 1
        self.tables_by_key: dict[str, list] = defaultdict(list)
        for e in m.entities.values():
            self.tables_by_key[_nat(self.schema_of.get(e.id), e.physical)].append(e)
        self.views_by_key: dict[str, list] = defaultdict(list)
        for v in m.views.values():
            self.views_by_key[_nat(self.schema_of.get(v.id), v.name)].append(v)
        self.schemas = {pol.schema_or_default(self.schema_of.get(oid)).upper()
                        for oid in (*m.entities, *m.views)}

    def score(self, oid: str) -> int:
        return pol.score_usage(self.x_rels[oid], self.x_canv[oid], self.x_views[oid])

    def winner(self, copies: list) -> object:
        return max(copies, key=lambda e: self.score(e.id))

    def internal_dups(self) -> list[dict]:
        out = []
        for key, copies in sorted(self.tables_by_key.items()):
            if len(copies) < 2:
                continue
            win = self.winner(copies)
            sigs = [_col_sig(e) for e in copies]
            out.append({"key": key, "copies": len(copies),
                        "identical": all(s == sigs[0] for s in sigs[1:]),
                        "scores": [self.score(e.id) for e in copies],
                        "winnerIndex": copies.index(win)})
        return out

    def partitions_to_reassign(self) -> list[str]:
        out = []
        for e in self.m.entities.values():
            vals = [(a.id, c) for a in e.attributes
                    if (c := pol.partition_correlative(
                        self.udp_vals.get((a.id, pol.PARTITION_UDP_KEY)))) is not None]
            if vals:
                _ids, motivo = pol.partition_marks(vals)
                if motivo:
                    out.append(f"{e.physical}: {motivo}")
        return out

    def unused_udp_defs(self) -> list[str]:
        used = {key for (_oid, key) in self.udp_vals}
        return sorted(f"{e['level']}|{e['name']}"
                      for k, e in self.collapsed.items() if k not in used)


def analyze_vs_db(fa: FileAnalysis, db) -> dict:
    """Solapes y estándares del archivo contra la BD viva (veredictos R1/R2)."""
    out: dict = {}
    ex_tables = {(t.get("schema") or "").upper() + "." + (t.get("physicalName") or "").upper(): t["_id"]
                 for t in db.canonical_tables.find(_ACTIVE, {"schema": 1, "physicalName": 1})}
    ex_views = {(v.get("schema") or "").upper() + "." + (v.get("name") or "").upper(): v["_id"]
                for v in db.views.find(_ACTIVE, {"schema": 1, "name": 1})}
    ex_schemas = {(s.get("name") or "").upper() for s in db.schemas.find(_ACTIVE, {"name": 1})}

    db_rel = Counter()
    for r in db.relationships.find(_ACTIVE, {"parentTableId": 1, "childTableId": 1}):
        db_rel[r.get("parentTableId")] += 1
        db_rel[r.get("childTableId")] += 1
    db_canv = Counter()
    for sa in db.subject_areas.find(_ACTIVE, {"tableIds": 1}):
        for tid in set(sa.get("tableIds") or []):
            db_canv[tid] += 1
    db_view = Counter()
    for v in db.views.find(_ACTIVE, {"sourceTableIds": 1}):
        for tid in set(v.get("sourceTableIds") or []):
            db_view[tid] += 1

    # tablas solapadas con veredicto + comparación de estructura
    overlaps = []
    matched_ids = []
    key_to_ent = {}
    for key, copies in fa.tables_by_key.items():
        tid = ex_tables.get(key)
        if not tid:
            continue
        win = fa.winner(copies)
        key_to_ent[key] = (win, tid)
        matched_ids.append(tid)
    db_cols: dict[str, list] = defaultdict(list)
    ids = sorted(set(matched_ids))
    for i in range(0, len(ids), 500):
        for c in db.canonical_columns.find(
                {**_ACTIVE, "tableId": {"$in": ids[i:i + 500]}},
                {"tableId": 1, "physicalName": 1}):
            db_cols[c["tableId"]].append((c.get("physicalName") or "").upper())
    for key, (win, tid) in sorted(key_to_ent.items()):
        sx = fa.score(win.id)
        sd = pol.score_usage(db_rel[tid], db_canv[tid], db_view[tid])
        cols_x = {a.physical.upper() for a in pol.dedupe_columns(win.attributes)[0]}
        cols_d = set(db_cols.get(tid, []))
        overlaps.append({
            "key": key, "scoreXml": sx, "scoreDb": sd,
            "verdict": "ACTUALIZA el doc vivo (gana el archivo)" if sx > sd
                       else "ADOPTA el doc vivo (gana la BD)",
            "identical": cols_x == cols_d,
            "colsOnlyXml": sorted(cols_x - cols_d)[:6],
            "colsOnlyDb": sorted(cols_d - cols_x)[:6],
        })
    out["tables_overlap"] = overlaps
    out["views_overlap"] = sorted(k for k in fa.views_by_key if k in ex_views)
    out["schemas_reused"] = sorted(s for s in fa.schemas if s in ex_schemas)
    out["schemas_new"] = len([s for s in fa.schemas if s not in ex_schemas])

    # estándares
    ex_gloss = {(g.get("term") or "").upper(): (g.get("abbrev") or "")
                for g in db.glossary_terms.find(_ACTIVE, {"term": 1, "abbrev": 1})}
    gl_new, gl_diff = [], []
    for g in fa.m.glossary:
        term = (g[0] or "").strip()
        ab = (g[1] if len(g) > 1 else "").strip()
        if not term:
            continue
        if term.upper() not in ex_gloss:
            gl_new.append(term)
        elif ab.upper() != ex_gloss[term.upper()].upper():
            gl_diff.append(f"{term}: BD={ex_gloss[term.upper()]} XML={ab}")
    out["glossary"] = {"new": gl_new, "abbrevDiff": gl_diff}

    ex_dom = {(d.get("name") or "").upper() for d in db.parent_domains.find(_ACTIVE, {"name": 1})}
    out["domains_new"] = sorted(d.name for d in fa.m.domains.values()
                                if not d.builtin and not d.name.startswith("<")
                                and d.name.upper() not in ex_dom)

    ex_udp = {((u.get("name") or "").upper(), u.get("level") or ""):
              (u.get("allowedValues") or [])
              for u in db.udp_definitions.find(_ACTIVE, {"name": 1, "level": 1,
                                                         "allowedValues": 1})}
    used = {key for (_oid, key) in fa.udp_vals}
    values_by_key: dict[str, set] = defaultdict(set)
    for (_oid, key), val in fa.udp_vals.items():
        values_by_key[key].add(val)
    udp_new, udp_skip, udp_merge, udp_variants = [], [], [], []
    for k, e in fa.collapsed.items():
        dbk = (e["name"].strip().upper(), e["level"])
        # los enums solo aplican a defs LIST (misma regla que migrate);
        # las de texto no convergen valores
        allowed: list[str] = []
        if e["dataType"] == "list":
            allowed = list(e.get("allowed_values") or [])
            allowed += pol.merge_allowed_values(
                allowed, sorted(values_by_key.get(k, set())
                                | ({e["default"]} if e["default"] else set())))
        if dbk not in ex_udp:
            (udp_new if k in used else udp_skip).append(f"{e['level']}|{e['name']}")
            continue
        add = pol.merge_allowed_values(ex_udp[dbk], allowed)
        if add:
            udp_merge.append(f"{e['level']}|{e['name']}: +{add}")
        norms = {pol.norm_enum(v): v for v in ex_udp[dbk]}
        vars_ = [f"{v!r}≈{norms[pol.norm_enum(v)]!r}" for v in allowed
                 if pol.norm_enum(v) in norms and v not in ex_udp[dbk]]
        if vars_:
            udp_variants.append(f"{e['level']}|{e['name']}: {vars_[:4]}")
    out["udp"] = {"createdWithUse": udp_new, "skippedA4": udp_skip,
                  "enumAdds": udp_merge, "caseVariantsIgnored": udp_variants}

    # estimación de carga (con la escritura bufferizada de migrate). Política
    # _DUPn (2026-08-22): TODAS las copias homónimas migran como tablas reales;
    # solo la keeper de una clave ADOPTADA no se re-escribe.
    adopted_keys = {o["key"] for o in overlaps if o["verdict"].startswith("ADOPTA")}
    n_tables = sum(len(c) for c in fa.tables_by_key.values()) - len(adopted_keys)
    n_cols = sum(len(pol.dedupe_columns(e.attributes)[0])
                 for k, copies in fa.tables_by_key.items()
                 for e in copies
                 if not (k in adopted_keys and e is fa.winner(copies)))
    n_rels = sum(1 for r in fa.m.relationships.values()
                 if r.rel_type in (ep.REL_IDENTIFYING, ep.REL_NON_IDENTIFYING,
                                   ep.REL_SUBTYPE))
    ops = (n_tables + n_cols + len(fa.views_by_key) + n_rels
           + len(fa.m.diagrams) + len(fa.m.subject_areas) + out["schemas_new"]
           + len(gl_new) + len(out["domains_new"]) + len(udp_new))
    t0 = time.perf_counter()
    for _ in range(3):
        db.canonical_tables.find_one({}, {"_id": 1})
    lat = (time.perf_counter() - t0) / 3
    batches = (ops + _BATCH - 1) // _BATCH
    out["load_estimate"] = {
        "upserts": ops, "batches": batches, "latency_ms": round(lat * 1000),
        "write_minutes": round(batches * 2 * lat / 60, 1),
    }
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Cross-check pre-carga: XML(s) vs BD viva y entre sí (solo lectura)")
    ap.add_argument("xml", nargs="+")
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--no-db", action="store_true",
                    help="sin conexión: solo análisis de archivo(s)")
    args = ap.parse_args(argv)

    db = None
    if not args.no_db:
        from dotenv import load_dotenv

        from app.core.db.sync import get_sync_db
        load_dotenv()
        db = get_sync_db()

    analyses: list[FileAnalysis] = []
    full: list[dict] = []
    for path in args.xml:
        print(f"\n{'=' * 72}\nARCHIVO: {path}")
        fa = FileAnalysis(path)
        analyses.append(fa)
        s = summarize(fa.m)
        print("  " + " · ".join(f"{k}={v}" for k, v in s.items()))
        rep: dict = {"file": path, "summary": s}

        dups = fa.internal_dups()
        rep["internal_dups"] = dups
        if dups:
            distintas = sum(1 for d in dups if not d["identical"])
            print(f"  duplicados internos: {len(dups)} claves "
                  f"({distintas} con estructura DISTINTA) → se migran TODAS; "
                  "las homónimas llevan sufijo _DUPn (política 2026-08-22)")
            # Transparencia total (2026-08-22): el detalle completo, sin resumir.
            for d in dups:
                mark = "⚠" if not d["identical"] else "≡"
                print(f"     {mark} {d['key']} ×{d['copies']} scores={d['scores']}")
        parts = fa.partitions_to_reassign()
        rep["partitions_reassign"] = parts
        if parts:
            print(f"  particiones a reasignar por orden físico (R6): {len(parts)}")
        unused = fa.unused_udp_defs()
        rep["udp_defs_unused"] = unused
        if unused:
            print(f"  defs UDP sin uso en el archivo: {len(unused)} "
                  "(las que no existan en BD se saltan — A4)")

        if db is not None:
            vs = analyze_vs_db(fa, db)
            rep["vs_db"] = vs
            ov = vs["tables_overlap"]
            print(f"  vs BD: tablas solapadas={len(ov)} · vistas={len(vs['views_overlap'])}"
                  f" · schemas reusados={len(vs['schemas_reused'])}"
                  f" (+{vs['schemas_new']} nuevos)")
            for o in ov:
                mark = "≡" if o["identical"] else "≠"
                print(f"     {mark} {o['key']} [xml={o['scoreXml']} vs bd={o['scoreDb']}]"
                      f" → {o['verdict']}")
                if not o["identical"]:
                    if o["colsOnlyXml"]:
                        print(f"        cols solo XML: {o['colsOnlyXml']}")
                    if o["colsOnlyDb"]:
                        print(f"        cols solo BD:  {o['colsOnlyDb']}")
            g = vs["glossary"]
            print(f"  estándares: glosario nuevos={len(g['new'])} "
                  f"abbrev≠={len(g['abbrevDiff'])} · dominios nuevos={len(vs['domains_new'])}")
            u = vs["udp"]
            print(f"  UDP: defs nuevas con uso={len(u['createdWithUse'])} · "
                  f"saltadas A4={len(u['skippedA4'])} · enums que crecen={len(u['enumAdds'])}")
            for x in u["enumAdds"]:
                print(f"     + {x}")
            for x in u["caseVariantsIgnored"]:
                print(f"     ≈ variante de grafía ignorada (A3): {x}")
            est = vs["load_estimate"]
            print(f"  carga estimada: ~{est['upserts']:,} upserts en {est['batches']} lotes"
                  f" (~{est['write_minutes']} min de escritura a {est['latency_ms']} ms/op)")
        full.append(rep)

    # solape ENTRE archivos (2+)
    if len(analyses) > 1:
        print(f"\n{'=' * 72}\nSOLAPE ENTRE ARCHIVOS")
        for i in range(len(analyses)):
            for j in range(i + 1, len(analyses)):
                inter = set(analyses[i].tables_by_key) & set(analyses[j].tables_by_key)
                print(f"  {analyses[i].path} ∩ {analyses[j].path}: {len(inter)} tablas")
                full.append({"pair": [analyses[i].path, analyses[j].path],
                             "tables_in_common": sorted(inter)})

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(full, fh, ensure_ascii=False, indent=1)
        print(f"\nReporte JSON: {args.json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
