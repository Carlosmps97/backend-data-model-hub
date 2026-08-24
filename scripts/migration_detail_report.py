"""Reporte Excel DETALLADO de la migración Erwin, objeto por objeto.

Complementa el reporte general (REPORTE-MIGRACION-ERWIN.md): para cada ajuste,
descarte o incongruencia de la última corrida de `run_migration.py` genera una
fila con la ubicación exacta en la plataforma (proyecto / carpeta / canvas),
pensado para que los modeladores validen visualmente contra el diagrama
homónimo de Erwin.

Fuentes (las tres se cruzan):
  1. `migration-reports/` — el último `run_migration-*.json` y el último
     reporte por XML de esa corrida (decisiones tabla por tabla).
  2. La base Lakebase — estado FINAL (post `audit_data_consistency --fix`):
     ubicaciones, relaciones desactivadas, llaves incompletas, columnas
     retiradas.
  3. Los XML de Erwin — relaciones/vistas que NO llegaron a la plataforma y
     en qué diagrama de Erwin verlas.

Uso (desde backend-data-model-hub/):
    .venv/bin/python -m scripts.migration_detail_report
    .venv/bin/python -m scripts.migration_detail_report \
        --xml-root ../folder_data --out ../REPORTE-MIGRACION-ERWIN-DETALLE.xlsx

`--cache-dir` guarda los modelos parseados (pickle) para re-ejecutar rápido.
Requiere `openpyxl` (está en el venv local; NO es dependencia de la app).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import pickle
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BACKEND)
load_dotenv(os.path.join(BACKEND, ".env"))

from app.core.db.sync import get_sync_db  # noqa: E402
from scripts.erwin_migration import erwin_parser as ep  # noqa: E402
from scripts.erwin_migration import policies as pol  # noqa: E402

ACTIVE = {"flgactive": {"$ne": False}}
LIMA = timezone(timedelta(hours=-5))
MAX_PATHS = 8
REL_TYPE_ES = {ep.REL_IDENTIFYING: "identificante", ep.REL_NON_IDENTIFYING: "no identificante",
               ep.REL_SUBTYPE: "subcategoría (supertipo→subtipo)", ep.REL_TABLE_TO_VIEW: "tabla→vista"}


def log(msg: str) -> None:
    print(msg, flush=True)


def lima(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).astimezone(LIMA).strftime("%d/%m/%Y %H:%M")
    except Exception:
        return iso or "?"


# ═══════════════════════════ descubrimiento de fuentes ═════════════════════
def discover(reports_dir: str, xml_root: str) -> list[dict]:
    """[{tag, project, domain, xml_path, report}] desde el último run_migration."""
    runs = sorted(glob.glob(os.path.join(reports_dir, "run_migration-*.json")))
    if not runs:
        raise SystemExit(f"No hay run_migration-*.json en {reports_dir}")
    with open(runs[-1]) as f:
        run = json.load(f)
    out = []
    for entry in run["files"]:
        rel = entry["rel"]
        base = os.path.basename(rel)
        pat = os.path.join(reports_dir, base.replace(" ", "_") + "-*.json")
        reps = sorted(glob.glob(pat))
        if not reps:
            raise SystemExit(f"No hay reporte por archivo para {base} ({pat})")
        with open(reps[-1]) as f:
            report = json.load(f)
        project, domain = entry["project"], entry["domain"]
        tag = f"{project.replace('Modelo ', '')} {domain.replace('Modelo ', '')}"
        out.append({
            "tag": tag, "project": project, "domain": domain, "report": report,
            "xml_path": os.path.join(xml_root, rel),
            "run": run,
        })
    return out


def parse_models(files: list[dict], cache_dir: str | None) -> None:
    for fi in files:
        path = fi["xml_path"]
        cache = None
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)
            st = os.stat(path)
            cache = os.path.join(
                cache_dir, f"{os.path.basename(path)}.{st.st_size}.{int(st.st_mtime)}.pkl")
        if cache and os.path.exists(cache):
            with open(cache, "rb") as f:
                fi["model"] = pickle.load(f)
            log(f"[xml] {fi['tag']}: cache")
            continue
        log(f"[xml] {fi['tag']}: parseando {os.path.getsize(path) // 1024 // 1024} MB …")
        fi["model"] = ep.parse(path)
        if cache:
            with open(cache, "wb") as f:
                pickle.dump(fi["model"], f)


# ═══════════════════════════ extracción ════════════════════════════════════
def extract(files: list[dict]) -> dict:
    models = {fi["tag"]: fi["model"] for fi in files}
    reports = {fi["tag"]: fi["report"] for fi in files}
    proj_of_tag = {fi["tag"]: fi["project"] for fi in files}
    dom_of_tag = {fi["tag"]: fi["domain"] for fi in files}

    schema_of = {tag: m.owner_schema() for tag, m in models.items()}
    ent_file: dict[str, list[str]] = {}
    rel_file: dict[str, str] = {}
    view_file: dict[str, list[str]] = {}
    key_files: dict[str, list[str]] = defaultdict(list)
    for tag, m in models.items():
        sof = schema_of[tag]
        for e in m.entities.values():
            ent_file.setdefault(e.id, []).append(tag)
            if e.physical:
                key_files[f"{pol.schema_or_default(sof.get(e.id))}.{e.physical}".upper()].append(tag)
        for v in m.views.values():
            view_file.setdefault(v.id, []).append(tag)
        for r in m.relationships.values():
            rel_file[r.id] = tag
    attr_ids_by_file = {tag: set(m.attr_index()) for tag, m in models.items()}

    rel_diags = {tag: defaultdict(list) for tag in models}
    obj_diags = {tag: defaultdict(list) for tag in models}
    for tag, m in models.items():
        rel_ids, known = set(m.relationships), set(m.entities) | set(m.views)
        for d in m.diagrams:
            for ref, _a in d.shapes:
                if ref in rel_ids:
                    rel_diags[tag][ref].append((d.subject_area, d.name))
                elif ref in known:
                    obj_diags[tag][ref].append((d.subject_area, d.name))

    db = get_sync_db()
    log("[db] proyectos/carpetas/canvases …")
    projects = {p["_id"]: p.get("name") for p in db.projects.find(ACTIVE, {"name": 1})}
    folders = {f["_id"]: f for f in db.folders.find(
        ACTIVE, {"name": 1, "parentFolderId": 1, "projectId": 1})}
    sas = {s["_id"]: s for s in db.subject_areas.find(
        {}, {"name": 1, "folderId": 1, "projectId": 1, "tableIds": 1, "layout": 1,
             "erwinLongId": 1, "udpValues": 1})}

    log("[db] tablas / columnas / relaciones / vistas …")
    tables = {t["_id"]: t for t in db.canonical_tables.find(
        {}, {"schema": 1, "physicalName": 1, "logicalName": 1, "erwinLongId": 1,
             "flgactive": 1, "udpValues": 1})}
    t_active = {tid for tid, t in tables.items() if t.get("flgactive") is not False}
    by_phys, by_key = {}, {}
    for tid in t_active:
        t = tables[tid]
        by_phys[(t.get("physicalName") or "").upper()] = tid
        by_key[((t.get("schema") or "").upper(), (t.get("physicalName") or "").upper())] = tid

    cols = {c["_id"]: c for c in db.canonical_columns.find(
        {}, {"tableId": 1, "physicalName": 1, "logicalName": 1, "isPrimaryKey": 1,
             "pkPosition": 1, "ordinal": 1, "flgactive": 1, "deletedAt": 1, "erwinLongId": 1})}
    c_active = {cid for cid, c in cols.items() if c.get("flgactive") is not False}
    pk_of_table: dict[str, list] = defaultdict(list)
    for cid in c_active:
        c = cols[cid]
        if c.get("isPrimaryKey"):
            pk_of_table[c["tableId"]].append(
                (c.get("pkPosition") if c.get("pkPosition") is not None else 999, cid))
    for tid in pk_of_table:
        pk_of_table[tid].sort()

    rels = {r["_id"]: r for r in db.relationships.find({}, {
        "parentTableId": 1, "childTableId": 1, "pairs": 1, "identifying": 1,
        "subcategory": 1, "flgactive": 1, "deletedAt": 1, "erwinLongId": 1})}
    r_active = {rid for rid, r in rels.items() if r.get("flgactive") is not False}
    views = {v["_id"]: v for v in db.views.find(ACTIVE, {
        "name": 1, "schema": 1, "sourceTableIds": 1, "sources": 1, "joinOverride": 1,
        "erwinLongId": 1})}
    udp_defs = {d["_id"]: d for d in db.udp_definitions.find(ACTIVE)}

    # ── ubicación ──────────────────────────────────────────────────────────
    def folder_chain(fid):
        names, seen = [], set()
        while fid and fid in folders and fid not in seen:
            seen.add(fid)
            names.append(folders[fid].get("name") or "?")
            fid = folders[fid].get("parentFolderId")
        return list(reversed(names))

    sa_path = {}
    for sid, s in sas.items():
        sa_path[sid] = (projects.get(s.get("projectId"), "?"),
                        " / ".join(folder_chain(s.get("folderId")) + [s.get("name") or "?"]))

    canvases_of_table: dict[str, list] = defaultdict(list)
    layout_of_node: dict[str, list] = defaultdict(list)
    for sid, s in sas.items():
        for tid in (s.get("tableIds") or []):
            canvases_of_table[tid].append(sid)
        for nid in (s.get("layout") or {}):
            layout_of_node[nid].append(sid)

    def paths_of(said_list):
        return sorted({sa_path[sid][1] for sid in said_list if sid in sa_path})

    def cap_paths(paths, empty="(no aparece en ningún canvas)"):
        if not paths:
            return empty
        if len(paths) <= MAX_PATHS:
            return "\n".join(paths)
        return "\n".join(paths[:MAX_PATHS] +
                         [f"… (+{len(paths) - MAX_PATHS} canvases más — total {len(paths)})"])

    def project_of(said_list, fallback=""):
        ps = sorted({sa_path[sid][0] for sid in said_list if sid in sa_path})
        return " y ".join(ps) if ps else fallback

    def table_loc(tid, fallback_proj=""):
        cs = canvases_of_table.get(tid, [])
        return project_of(cs, fallback_proj), cap_paths(paths_of(cs))

    def rel_canvases(parent_tid, child_tid):
        both = set(canvases_of_table.get(parent_tid, [])) & set(canvases_of_table.get(child_tid, []))
        return project_of(both), paths_of(both)

    def colname(cid, with_dead_mark=True):
        c = cols.get(cid)
        if not c:
            return f"(columna inexistente {str(cid)[:8]})"
        n = c.get("physicalName") or "?"
        if with_dead_mark and c.get("flgactive") is False:
            n += " (columna retirada)"
        return n

    def tname(tid):
        t = tables.get(tid)
        return f"{t.get('schema')}.{t.get('physicalName')}" if t else f"(tabla inexistente {str(tid)[:8]})"

    OUT: dict = {}

    # ═══ S1 · renombradas _DUPn ════════════════════════════════════════════
    rows = []
    for tag in models:
        proj = proj_of_tag[tag]
        for g in reports[tag]["decisions"]["renamed_dups"]:
            schema_raw, _orig = g["key"].split(".", 1)
            kept_tid = by_phys.get((g["kept"] or "").upper())
            _kp, kept_canv = table_loc(kept_tid, proj) if kept_tid else (proj, "?")
            for r in g["renamed"]:
                tid = by_phys.get(r["to"].upper())
                t = tables.get(tid, {})
                p, canv = table_loc(tid, proj) if tid else (proj, "?")
                rows.append({
                    "archivo": tag, "proyecto": p or proj,
                    "esquema": t.get("schema") or schema_raw,
                    "nombre_original": r["from"], "nombre_final": r["to"],
                    "nombre_logico": r.get("logicalName") or t.get("logicalName") or "",
                    "copias": g["copies"], "conservo_nombre": g["kept"],
                    "canvases": canv, "canvases_kept": kept_canv,
                })
    OUT["renombradas"] = rows
    log(f"[S1] renombradas: {len(rows)}")

    # ═══ S2 · unificadas entre archivos ════════════════════════════════════
    rows = []
    for tag in models:
        proj = proj_of_tag[tag]
        dec = reports[tag]["decisions"]
        unmapped = {a["key"]: a.get("columnsUnmapped", 0) for a in dec["adopted"]}
        for c in dec["conflicts"]:
            schema_raw, phys = c["key"].split(".", 1)
            tid = by_key.get((schema_raw.upper(), phys.upper())) or by_phys.get(phys.upper())
            t = tables.get(tid, {})
            p, canv = table_loc(tid, proj) if tid else (proj, "?")
            res = ("Se conservó la versión ya cargada (este archivo se enganchó a ella)"
                   if c["won"] == "db"
                   else "Ganó la versión de este archivo: la tabla se actualizó en su sitio")
            rows.append({
                "archivo": tag, "proyecto": p or proj,
                "esquema": t.get("schema") or schema_raw, "tabla": phys,
                "nombre_logico": t.get("logicalName") or "",
                "usos_archivo": c["scoreXml"], "usos_bd": c["scoreDb"], "resultado": res,
                "cols_sin_equivalente": unmapped.get(c["key"], 0) if c["won"] == "db" else "",
                "archivos": ", ".join(sorted(set(key_files.get(c["key"].upper(), [])))),
                "canvases": canv,
            })
    OUT["unificadas"] = rows
    log(f"[S2] unificadas/conflictos: {len(rows)}")

    # ═══ S3 · columnas retiradas por la unificación ════════════════════════
    dead_col_ids = [cid for cid, c in cols.items() if c.get("flgactive") is False]
    rel_dead_pairs: dict[str, list] = defaultdict(list)
    for rid, r in rels.items():
        if r.get("flgactive") is False:
            for pr in (r.get("pairs") or []):
                rel_dead_pairs[pr.get("parentColumnId")].append(rid)
                rel_dead_pairs[pr.get("childColumnId")].append(rid)
    rows = []
    for cid in dead_col_ids:
        c = cols[cid]
        tid = c.get("tableId")
        t = tables.get(tid, {})
        p, canv = table_loc(tid)
        aid = c.get("erwinLongId")
        src_tags = [tg for tg, aidset in attr_ids_by_file.items() if aid and aid in aidset]
        rows.append({
            "proyecto": p, "esquema": t.get("schema") or "?",
            "tabla": t.get("physicalName") or "?",
            "columna": c.get("physicalName") or "?",
            "columna_logica": c.get("logicalName") or "",
            "era_pk": "Sí" if c.get("isPrimaryKey") else "No",
            "venia_de": ", ".join(src_tags) or "",
            "afecta_relacion": ("Sí — ver hoja «Relaciones desactivadas»"
                                if rel_dead_pairs.get(cid) else "No"),
            "canvases": canv,
        })
    rows.sort(key=lambda x: (x["tabla"], x["columna"]))
    OUT["cols_retiradas"] = rows
    log(f"[S3] columnas retiradas: {len(rows)}")

    # ═══ S4 · relaciones desactivadas por el fix ═══════════════════════════
    rows = []
    for rid, r in rels.items():
        if r.get("flgactive") is not False:
            continue
        pt, ct = r.get("parentTableId"), r.get("childTableId")
        proj, canvs = rel_canvases(pt, ct)
        pares = [f"{colname(pr.get('parentColumnId'))} → {colname(pr.get('childColumnId'))}"
                 for pr in (r.get("pairs") or [])]
        twin = any(rid2 in r_active and rels[rid2].get("parentTableId") == pt
                   and rels[rid2].get("childTableId") == ct for rid2 in rels if rid2 != rid)
        erw = r.get("erwinLongId")
        tag_of = rel_file.get(erw)
        xml_rel = models[tag_of].relationships.get(erw) if tag_of else None
        rows.append({
            "relacion_erwin": xml_rel.name if xml_rel else "", "archivo": tag_of or "",
            "proyecto": proj, "tabla_padre": tname(pt), "tabla_hija": tname(ct),
            "pares": "\n".join(pares),
            "tipo": "identificante" if r.get("identifying") else "no identificante",
            "gemela": "Sí" if twin else "No",
            "canvases": cap_paths(canvs, "(las tablas no comparten canvas)"),
        })
    rows.sort(key=lambda x: (x["tabla_padre"], x["tabla_hija"]))
    OUT["desactivadas"] = rows
    log(f"[S4] relaciones desactivadas: {len(rows)}")

    # ═══ S5 · llave incompleta (ROJO) ══════════════════════════════════════
    rows = []
    for rid in r_active:
        r = rels[rid]
        pt, ct = r.get("parentTableId"), r.get("childTableId")
        pk_ids = [cid for _pos, cid in pk_of_table.get(pt, [])]
        in_pairs = {pr.get("parentColumnId") for pr in (r.get("pairs") or [])}
        missing = [cid for cid in pk_ids if cid not in in_pairs]
        extra = [pr.get("parentColumnId") for pr in (r.get("pairs") or [])
                 if pr.get("parentColumnId") not in set(pk_ids)]
        if not missing and not extra:
            continue
        proj, canvs = rel_canvases(pt, ct)
        erw = r.get("erwinLongId")
        tag_of = rel_file.get(erw)
        xml_rel = models[tag_of].relationships.get(erw) if tag_of else None
        rows.append({
            "relacion_erwin": xml_rel.name if xml_rel else "", "archivo": tag_of or "",
            "proyecto": proj, "tabla_padre": tname(pt), "tabla_hija": tname(ct),
            "subcategoria": "Sí" if r.get("subcategory") else "No",
            "pares": "\n".join(
                f"{colname(pr.get('parentColumnId'), False)} → {colname(pr.get('childColumnId'), False)}"
                for pr in (r.get("pairs") or [])),
            "pk_faltante": "\n".join(colname(cid, False) for cid in missing) or "—",
            "extra_fuera_pk": "\n".join(colname(cid, False) for cid in extra) or "—",
            "n_canvases": len(canvs),
            "canvases": cap_paths(canvs, "(las tablas no comparten canvas)"),
        })
    rows.sort(key=lambda x: (-x["n_canvases"], x["tabla_padre"]))
    OUT["llave_incompleta"] = rows
    log(f"[S5] llave incompleta vigentes: {len(rows)}")

    # (conciliación) rojas contando también columnas PK retiradas
    pk_all: dict[str, set] = defaultdict(set)
    for cid, c in cols.items():
        if c.get("isPrimaryKey"):
            pk_all[c["tableId"]].add(cid)
    rojas_con_muertas = 0
    for rid in r_active:
        r = rels[rid]
        pks = pk_all.get(r.get("parentTableId"), set())
        inp = {p.get("parentColumnId") for p in (r.get("pairs") or [])}
        if (pks - inp) or (inp - pks):
            rojas_con_muertas += 1

    # ═══ S6 · relaciones no migradas ═══════════════════════════════════════
    db_rel_erwin = {r.get("erwinLongId") for r in rels.values()}
    reused_by_file = {tag: {e["key"] for e in reports[tag]["decisions"]["rels_reused"]}
                      for tag in models}
    rows = []
    for tag, m in models.items():
        proj, dom = proj_of_tag[tag], dom_of_tag[tag]
        sof = schema_of[tag]
        for r in m.relationships.values():
            broken, motivo = False, ""
            if r.rel_type == ep.REL_TABLE_TO_VIEW:
                if r.parent_ref not in m.entities or r.child_ref not in m.views:
                    broken = True
                    partes = []
                    if r.parent_ref not in m.entities:
                        partes.append("el extremo padre no es una tabla del XML" +
                                      (" (es una vista)" if r.parent_ref in m.views else " (no existe)"))
                    if r.child_ref not in m.views:
                        partes.append("el extremo hijo no existe como vista en el XML")
                    motivo = "Relación tabla→vista rota: " + " y ".join(partes)
                else:
                    continue
            elif r.rel_type not in (ep.REL_IDENTIFYING, ep.REL_NON_IDENTIFYING, ep.REL_SUBTYPE):
                continue
            elif r.parent_ref not in m.entities or r.child_ref not in m.entities:
                broken = True
                motivo = "Relación con un extremo inexistente en el XML (rota de origen)"
            if not broken:
                if r.id in db_rel_erwin or r.name in reused_by_file[tag]:
                    continue
                motivo = ("Sin pares de columnas FK resolubles: no une columna con columna "
                          "(la plataforma exige columna origen y destino)")

            def _nm(ref):
                if ref in m.entities:
                    e = m.entities[ref]
                    return f"{pol.schema_or_default(sof.get(ref))}.{e.physical}", e.name
                if ref in m.views:
                    return f"{pol.schema_or_default(sof.get(ref))}.{m.views[ref].name} (vista)", ""
                return "(no existe en el XML)", ""

            pn, pl = _nm(r.parent_ref)
            cn, cl = _nm(r.child_ref)
            diags = rel_diags[tag].get(r.id) or []
            if not diags:
                diags = sorted(set(obj_diags[tag].get(r.parent_ref, []))
                               & set(obj_diags[tag].get(r.child_ref, [])))
            rows.append({
                "archivo": tag, "proyecto": proj,
                "relacion_erwin": r.name, "tipo": REL_TYPE_ES.get(r.rel_type, r.rel_type),
                "tabla_padre": pn, "padre_logico": pl, "tabla_hija": cn, "hija_logico": cl,
                "motivo": motivo,
                "estado": "Rota de origen — NO migrada" if broken else "NO migrada",
                "donde_erwin": cap_paths(
                    [f"{dom} / {sa} / {dg}" for sa, dg in sorted(set(diags))],
                    "(no está dibujada en ningún diagrama del XML)"),
            })
    OUT["no_migradas"] = rows
    log(f"[S6] no migradas: {len(rows)}")

    # ═══ S7 · columnas duplicadas descartadas ══════════════════════════════
    rows = []
    for tag in models:
        proj = proj_of_tag[tag]
        for d in reports[tag]["decisions"]["cols_dropped"]:
            schema_raw, phys = d["table"].split(".", 1)
            tid = by_key.get((schema_raw.upper(), phys.upper())) or by_phys.get(phys.upper())
            t = tables.get(tid, {})
            p, canv = table_loc(tid, proj) if tid else (proj, "?")
            rows.append({"archivo": tag, "proyecto": p or proj,
                         "esquema": t.get("schema") or schema_raw, "tabla": phys,
                         "columna": d["column"], "canvases": canv})
    OUT["cols_duplicadas"] = rows
    log(f"[S7] columnas duplicadas descartadas: {len(rows)}")

    # ═══ S8 · particiones reasignadas ══════════════════════════════════════
    rows = []
    for tag in models:
        proj = proj_of_tag[tag]
        for d in reports[tag]["decisions"]["partitions_reassigned"]:
            schema_raw, phys = d["table"].split(".", 1)
            tid = by_key.get((schema_raw.upper(), phys.upper())) or by_phys.get(phys.upper())
            t = tables.get(tid, {})
            p, canv = table_loc(tid, proj) if tid else (proj, "?")
            detail = d["detail"]
            corr = (detail.split("correlativos leídos:")[-1].split(")")[0].strip()
                    if "correlativos leídos:" in detail else detail)
            rows.append({"archivo": tag, "proyecto": p or proj,
                         "esquema": t.get("schema") or schema_raw, "tabla": phys,
                         "correlativos": corr, "canvases": canv})
    OUT["particiones"] = rows
    log(f"[S8] particiones reasignadas: {len(rows)}")

    # ═══ S9 · vistas ═══════════════════════════════════════════════════════
    rows = []
    for tag, m in models.items():          # a) descartadas
        proj, dom = proj_of_tag[tag], dom_of_tag[tag]
        sof = schema_of[tag]
        name_to_view = defaultdict(list)
        for v in m.views.values():
            name_to_view[v.name].append(v)
        for d in reports[tag]["decisions"]["views_discarded"]:
            diags, schema = [], ""
            for v in name_to_view.get(d["view"], []):
                schema = pol.schema_or_default(sof.get(v.id))
                diags += obj_diags[tag].get(v.id, [])
            rows.append({
                "archivo": tag, "proyecto": proj,
                "caso": "Descartada (sin tabla fuente resoluble)",
                "esquema": schema, "vista": d["view"],
                "detalle": "No existe en la plataforma: ninguna de sus fuentes se pudo "
                           "resolver a una tabla física",
                "canvases": cap_paths([f"{dom} / {sa} / {dg}" for sa, dg in sorted(set(diags))],
                                      "(no está dibujada en ningún diagrama del XML)"),
            })
    n_dup_copias = 0
    for tag, m in models.items():          # b) duplicadas → queda una sola
        proj = proj_of_tag[tag]
        sof = schema_of[tag]
        groups = defaultdict(list)
        for v in m.views.values():
            groups[(pol.schema_or_default(sof.get(v.id)).upper(), v.name.upper())].append(v)
        for (sch, nm), copies in sorted(groups.items()):
            if len(copies) < 2:
                continue
            n_dup_copias += len(copies) - 1
            vdoc = next((v for v in views.values()
                         if (v.get("schema") or "").upper() == sch
                         and (v.get("name") or "").upper() == nm), None)
            canv = cap_paths(paths_of(layout_of_node.get(vdoc["_id"], []))) if vdoc else "?"
            rows.append({
                "archivo": tag, "proyecto": proj, "caso": "Duplicada (quedó una sola)",
                "esquema": pol.schema_or_default(sof.get(copies[0].id)),
                "vista": copies[0].name,
                "detalle": f"Venía {len(copies)} veces en el archivo; en la plataforma queda "
                           f"una sola (la copia más usada/completa)",
                "canvases": canv,
            })
    rel_pairs_set = {frozenset((rels[rid].get("parentTableId"), rels[rid].get("childTableId")))
                     for rid in r_active}
    n_multi = 0
    for vid, v in views.items():           # c) multi-fuente sin join (audit C5)
        alive = [s for s in (v.get("sourceTableIds") or []) if s in t_active]
        if len(alive) <= 1 or (v.get("joinOverride") or "").strip():
            continue
        if all(any(frozenset((a, b)) in rel_pairs_set for b in alive if b != a) for a in alive):
            continue
        n_multi += 1
        fuentes = "\n".join(sorted(tname(s) for s in alive))
        tag_of = (view_file.get(v.get("erwinLongId")) or ["?"])[0]
        rows.append({
            "archivo": tag_of, "proyecto": project_of(layout_of_node.get(vid, [])),
            "caso": "Multi-fuente sin join declarado",
            "esquema": v.get("schema") or "", "vista": v.get("name") or "",
            "detalle": "Tiene 2+ tablas fuente sin join declarado ni relación entre ellas; "
                       "declarar el join en el editor de vistas\nFuentes:\n" + fuentes,
            "canvases": cap_paths(paths_of(layout_of_node.get(vid, []))),
        })
    n_vcol = 0
    for tag, m in models.items():          # d) columnas de vista sin origen
        proj, dom = proj_of_tag[tag], dom_of_tag[tag]
        sof = schema_of[tag]
        for v in m.views.values():
            for a in v.attributes:
                if a.parent_attr_ref:
                    continue
                n_vcol += 1
                rows.append({
                    "archivo": tag, "proyecto": proj, "caso": "Columna de vista sin origen",
                    "esquema": pol.schema_or_default(sof.get(v.id)),
                    "vista": f"{v.name}.{a.physical}",
                    "detalle": "La columna no pudo vincularse a su columna de origen; "
                               "se migró con definición propia, sin vínculo",
                    "canvases": cap_paths(
                        [f"{dom} / {sa} / {dg}"
                         for sa, dg in sorted(set(obj_diags[tag].get(v.id, [])))], "?"),
                })
    OUT["vistas"] = rows
    OUT["_vistas_nums"] = {"dup_copias": n_dup_copias, "multi": n_multi, "vcol": n_vcol}
    log(f"[S9] vistas con observaciones: {len(rows)}")

    # ═══ S10 · tablas sin canvas ═══════════════════════════════════════════
    rows = []
    for tid in sorted(t_active, key=lambda x: tname(x)):
        if canvases_of_table.get(tid):
            continue
        t = tables[tid]
        erw = t.get("erwinLongId")
        kk = f"{(t.get('schema') or '')}.{(t.get('physicalName') or '')}".upper()
        tags = sorted(set(ent_file.get(erw, [])) | set(key_files.get(kk, [])))
        proj = ", ".join(sorted({proj_of_tag[g] for g in tags})) if tags else ""
        drawn = [(g, sa, dg) for g in tags for sa, dg in obj_diags[g].get(erw, [])]
        if drawn:
            donde = "; ".join(f"«{sa} / {dg}» del {g}" for g, sa, dg in sorted(set(drawn)))
            nota = (f"En Erwin está dibujada en {donde}, pero ese canvas se unificó con el "
                    f"diagrama homónimo del otro archivo (mismo identificador Erwin) y "
                    f"quedaron solo las figuras de la versión del último archivo cargado. "
                    f"Si debe verse, volver a arrastrarla al canvas.")
        else:
            nota = ("No está dibujada en ningún diagrama de Erwin. Se migró igual: se "
                    "encuentra por el buscador o el explorador de BD.")
        rows.append({"proyecto": proj, "archivo": ", ".join(tags),
                     "esquema": t.get("schema") or "", "tabla": t.get("physicalName") or "",
                     "nombre_logico": t.get("logicalName") or "", "nota": nota})
    OUT["sin_canvas"] = rows
    OUT["_sin_canvas_dibujadas"] = sum(1 for r in rows if "unificó" in r["nota"])
    log(f"[S10] tablas sin canvas: {len(rows)}")

    # ═══ S11 · canvases vacíos ═════════════════════════════════════════════
    rows = []
    for sid, s in sas.items():
        if (s.get("tableIds") or []) or (s.get("layout") or {}):
            continue
        proj, path = sa_path[sid]
        nota = "El diagrama venía vacío en Erwin; el canvas existe sin ninguna figura."
        erw = s.get("erwinLongId")
        for tag, m in models.items():
            for d in m.diagrams:
                if d.id == erw and d.shapes:
                    drawn = sorted({m.entities[ref].physical for ref, _a in d.shapes
                                    if ref in m.entities})
                    if drawn:
                        nota = (f"En el {tag} este mismo diagrama dibujaba {len(drawn)} tablas "
                                f"({', '.join(drawn)}); el canvas unificado conservó la versión "
                                f"vacía del último archivo cargado. Si deben verse, volver a "
                                f"arrastrarlas al canvas.")
        rows.append({"proyecto": proj, "canvas": path, "nota": nota})
    rows.sort(key=lambda x: (x["proyecto"], x["canvas"]))
    OUT["canvases_vacios"] = rows
    log(f"[S11] canvases vacíos: {len(rows)}")

    # ═══ S12 · esquema no definido (archivos DDV) ══════════════════════════
    # Objetos sin Hive_Database en el XML que siguen en «No_Definido». En el
    # UDV eso es lo esperado (no maneja esquemas) — solo se listan los DDV.
    rows = []
    n_t = n_v_desc = 0
    by_erwin_t = {tables[tid].get("erwinLongId"): tid for tid in t_active}
    vby_erwin = {v.get("erwinLongId"): vid for vid, v in views.items()}
    vby_name = {}
    for vid, v in views.items():
        vby_name.setdefault((v.get("name") or "").upper(), vid)
    for tag, m in models.items():
        if not proj_of_tag[tag].upper().endswith("DDV"):
            continue
        sof = schema_of[tag]
        for e in sorted(m.entities.values(), key=lambda e: e.physical or ""):
            if e.id in sof:
                continue
            tid = by_erwin_t.get(e.id) or by_phys.get((e.physical or "").upper())
            if not tid or (tables[tid].get("schema") or "").upper() != pol.NO_SCHEMA.upper():
                continue
            t = tables[tid]
            p, canv = table_loc(tid, proj_of_tag[tag])
            rows.append({"tipo": "Tabla", "proyecto": p or proj_of_tag[tag],
                         "nombre": t.get("physicalName") or "",
                         "nombre_logico": t.get("logicalName") or "", "canvases": canv})
            n_t += 1
        for xv in sorted(m.views.values(), key=lambda v: v.name or ""):
            if xv.id in sof:
                continue
            vid = vby_erwin.get(xv.id) or vby_name.get((xv.name or "").upper())
            if not vid:
                n_v_desc += 1
                continue
            v = views[vid]
            if (v.get("schema") or "").upper() != pol.NO_SCHEMA.upper():
                continue
            rows.append({"tipo": "Vista",
                         "proyecto": project_of(layout_of_node.get(vid, []), proj_of_tag[tag]),
                         "nombre": v.get("name") or "", "nombre_logico": "",
                         "canvases": cap_paths(paths_of(layout_of_node.get(vid, [])))})
    OUT["no_definido"] = rows
    OUT["_no_definido_nums"] = {"tablas": n_t, "vistas": len(rows) - n_t,
                                "vistas_descartadas": n_v_desc}
    log(f"[S12] esquema no definido (DDV): {n_t} tablas + {len(rows) - n_t} vistas")

    # ═══ S13 · UDP fuera de catálogo ═══════════════════════════════════════
    rows = []
    list_defs = {did: d for did, d in udp_defs.items()
                 if d.get("dataType") == "list" and d.get("allowedValues")}

    def _catalogo(d, val):
        return next((av for av in d["allowedValues"]
                     if av.strip().lower() == (val or "").strip().lower()), "")

    for tid in t_active:
        for k, val in (tables[tid].get("udpValues") or {}).items():
            d = list_defs.get(k)
            if not d or val in d["allowedValues"]:
                continue
            p, canv = table_loc(tid)
            rows.append({"nivel": "Tabla", "proyecto": p, "entidad": tname(tid),
                         "udp": d.get("name"), "valor": val,
                         "valor_catalogo": _catalogo(d, val) or "(sin equivalente)",
                         "canvases": canv})
    for sid, s in sas.items():
        for k, val in (s.get("udpValues") or {}).items():
            d = list_defs.get(k)
            if not d or val in d["allowedValues"]:
                continue
            p, path = sa_path[sid]
            rows.append({"nivel": "Canvas", "proyecto": p, "entidad": f"(canvas) {path}",
                         "udp": d.get("name"), "valor": val,
                         "valor_catalogo": _catalogo(d, val) or "(sin equivalente)",
                         "canvases": path})
    for c in db.canonical_columns.find({**ACTIVE, "udpValues": {"$exists": True, "$ne": {}}},
                                       {"tableId": 1, "physicalName": 1, "udpValues": 1}):
        for k, val in (c.get("udpValues") or {}).items():
            d = list_defs.get(k)
            if not d or val in d["allowedValues"]:
                continue
            p, canv = table_loc(c.get("tableId"))
            rows.append({"nivel": "Columna", "proyecto": p,
                         "entidad": f"{tname(c.get('tableId'))}.{c.get('physicalName')}",
                         "udp": d.get("name"), "valor": val,
                         "valor_catalogo": _catalogo(d, val) or "(sin equivalente)",
                         "canvases": canv})
    rows.sort(key=lambda x: (x["udp"], x["nivel"], x["entidad"]))
    OUT["udp_fuera_catalogo"] = rows
    log(f"[S13] UDP fuera de catálogo: {len(rows)}")

    # ═══ S14 · defs UDP omitidas ═══════════════════════════════════════════
    agg: dict = {}
    for tag in models:
        for d in reports[tag]["decisions"]["udp_defs_skipped"]:
            key = (d["name"], d["level"])
            agg.setdefault(key, {"name": d["name"], "level": d["level"],
                                 "dataType": d.get("dataType") or "", "files": []})
            agg[key]["files"].append(tag)
    OUT["udp_omitidas"] = [
        {"udp": v["name"], "nivel": v["level"], "tipo": v["dataType"],
         "archivos": ", ".join(v["files"]),
         "nota": "Definición sin ningún uso en el archivo: no se migró"}
        for v in sorted(agg.values(), key=lambda x: x["name"])]
    log(f"[S14] defs UDP omitidas: {len(OUT['udp_omitidas'])}")

    # ═══ meta / resumen ════════════════════════════════════════════════════
    dead_rels = [r for r in rels.values() if r.get("flgactive") is False]
    run = files[0]["run"]
    OUT["meta"] = {
        "corrida_inicio": lima(run.get("startedAt", "")),
        "corrida_fin": lima(run.get("finishedAt", "")),
        "fix_at": lima(min((r.get("deletedAt") or "" for r in dead_rels), default="")) if dead_rels else "",
        "archivos": [{
            "tag": fi["tag"], "xml": os.path.basename(fi["xml_path"]),
            "destino": f"{fi['project']} → {fi['domain']}",
            "tablas": fi["report"]["stats"].get("tablas", 0),
            "columnas": fi["report"]["stats"].get("columnas", 0),
            "vistas": fi["report"]["stats"].get("vistas", 0),
            "relaciones": fi["report"]["stats"].get("relaciones", 0),
            "canvases": fi["report"]["stats"].get("canvases", 0),
        } for fi in files],
        "totales": {
            "proyectos": len(projects), "carpetas": len(folders), "canvases": len(sas),
            "tablas": len(t_active), "columnas": len(c_active), "vistas": len(views),
            "relaciones_activas": len(r_active),
            "subcategorias": sum(1 for rid in r_active if rels[rid].get("subcategory")),
            "relaciones_desactivadas_fix": len(dead_rels),
            "columnas_retiradas": len(dead_col_ids),
            "relaciones_rojas_vigentes": len(OUT["llave_incompleta"]),
            "rojas_contando_pk_retiradas": rojas_con_muertas,
        },
    }
    return OUT


# ═══════════════════════════ Excel ═════════════════════════════════════════
def build_excel(D: dict, out_path: str) -> None:
    from openpyxl import Workbook
    from openpyxl.formatting.rule import FormulaRule
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    AZUL = "1F4E78"
    HDR_FONT = Font(bold=True, color="FFFFFF", size=10)
    HDR_FILL = PatternFill("solid", fgColor=AZUL)
    TITLE_FONT = Font(bold=True, size=14, color=AZUL)
    DESC_FONT = Font(italic=True, size=10, color="555555")
    THIN = Side(style="thin", color="C9D2DC")
    BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
    WRAP_TOP = Alignment(wrap_text=True, vertical="top")
    TOP = Alignment(vertical="top")
    CENTER = Alignment(horizontal="center", vertical="top")
    TAB = {"resumen": "1F4E78", "tabla": "2E75B6", "relacion": "C0504D",
           "vista": "31859C", "otro": "7F7F7F"}

    wb = Workbook()
    wb.remove(wb.active)

    def add_sheet(name, tab_kind, title, desc, headers, widths, rows, keys,
                  wrap_cols=(), num_cols=(), center_cols=(), max_h=150):
        ws = wb.create_sheet(name)
        ws.sheet_properties.tabColor = TAB[tab_kind]
        last = get_column_letter(len(headers))
        ws["A1"] = title
        ws["A1"].font = TITLE_FONT
        ws.merge_cells(f"A2:{last}2")
        c = ws["A2"]
        c.value = desc
        c.font = DESC_FONT
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[2].height = 14 * (desc.count("\n") + max(1, len(desc) // 160) + 1)
        hrow = 4
        for j, h in enumerate(headers, 1):
            cell = ws.cell(row=hrow, column=j, value=h)
            cell.font, cell.fill, cell.border = HDR_FONT, HDR_FILL, BORDER
            cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        ws.row_dimensions[hrow].height = 30
        for i, r in enumerate(rows):
            row_idx = hrow + 1 + i
            lines = 1
            for j, k in enumerate(keys, 1):
                v = r.get(k, "")
                cell = ws.cell(row=row_idx, column=j, value=v)
                cell.border = BORDER
                if j in num_cols or j in center_cols:
                    cell.alignment = CENTER
                elif j in wrap_cols:
                    cell.alignment = WRAP_TOP
                    if isinstance(v, str):
                        lines = max(lines, v.count("\n") + 1)
                else:
                    cell.alignment = TOP
            if lines > 1:
                ws.row_dimensions[row_idx].height = min(13.5 * lines + 4, max_h)
        for j, w in enumerate(widths, 1):
            ws.column_dimensions[get_column_letter(j)].width = w
        if rows:
            ws.auto_filter.ref = f"A{hrow}:{last}{hrow + len(rows)}"
            ws.conditional_formatting.add(
                f"A{hrow + 1}:{last}{hrow + len(rows)}",
                FormulaRule(formula=["MOD(ROW(),2)=0"],
                            fill=PatternFill("solid", fgColor="F2F5F9")))
        ws.freeze_panes = f"A{hrow + 1}"

    # ── Resumen ────────────────────────────────────────────────────────────
    meta = D["meta"]
    t = meta["totales"]
    ws = wb.create_sheet("Resumen")
    ws.sheet_properties.tabColor = TAB["resumen"]
    ws.column_dimensions["A"].width = 3
    for col, w in (("B", 34), ("C", 16), ("D", 96), ("E", 14)):
        ws.column_dimensions[col].width = w
    r = 2
    ws.cell(row=r, column=2, value="Migración Erwin → Data Model Hub").font = \
        Font(bold=True, size=16, color=AZUL)
    r += 1
    ws.cell(row=r, column=2,
            value="Detalle por objeto, con la corrección posterior ya aplicada").font = \
        Font(size=11, color="555555")
    r += 2
    fix_txt = (f"{meta['fix_at']} (hora de Lima). El comando de corrección desactivó las "
               f"{t['relaciones_desactivadas_fix']} relaciones que quedaron colgando de la "
               f"unificación de tablas entre archivos (hoja «Relaciones desactivadas»). "
               f"Ningún otro objeto cambió.") if meta["fix_at"] else \
        "No se registra ninguna corrección aplicada (no hay relaciones desactivadas)."
    intro = [
        ("Corrida de migración",
         f"{meta['corrida_inicio']} – {meta['corrida_fin'].split(' ')[-1]} (hora de Lima). La base se vació por "
         "completo antes de cargar (reset destructivo): todo lo descrito corresponde únicamente a esta corrida "
         "de los archivos XML listados abajo."),
        ("Corrección aplicada", fix_txt),
        ("Qué contiene este archivo",
         "Cada hoja lista un tipo de ajuste, descarte o incongruencia detectada al migrar, objeto por objeto, "
         "con su ubicación exacta en la plataforma: proyecto y canvases donde aparece "
         "(ruta Carpeta / Subcarpeta / Canvas)."),
        ("Cómo validar",
         "1) Ubicar el objeto con la columna «Canvases donde aparece» y abrirlo en la plataforma "
         "(Proyecto → Carpeta → Canvas).  2) Abrir en Erwin el mismo modelo / subject area / diagrama "
         "(los nombres coinciden).  3) Comparar visualmente: lo que esté en una hoja de este archivo es "
         "exactamente lo que puede verse distinto entre ambos."),
    ]
    for titulo, texto in intro:
        c = ws.cell(row=r, column=2, value=titulo)
        c.font = Font(bold=True, size=10)
        c.alignment = Alignment(vertical="top")
        ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=4)
        c2 = ws.cell(row=r, column=3, value=texto)
        c2.alignment = Alignment(wrap_text=True, vertical="top")
        c2.font = Font(size=10)
        ws.row_dimensions[r].height = 13 * (len(texto) // 105 + 1) + 6
        r += 1
    r += 1

    ws.cell(row=r, column=2, value="Archivos migrados (números del reporte de cada archivo)").font = \
        Font(bold=True, size=12, color=AZUL)
    r += 1
    hdr = ["Nombre corto", "Archivo XML", "Proyecto → carpeta", "Tablas", "Columnas",
           "Vistas", "Relaciones", "Canvases"]
    for j, h in enumerate(hdr, 2):
        c = ws.cell(row=r, column=j, value=h)
        c.font, c.fill, c.border = HDR_FONT, HDR_FILL, BORDER
    r += 1
    for fi in meta["archivos"]:
        vals = [fi["tag"], fi["xml"], fi["destino"], fi["tablas"], fi["columnas"],
                fi["vistas"], fi["relaciones"], fi["canvases"]]
        for j, v in enumerate(vals, 2):
            c = ws.cell(row=r, column=j, value=v)
            c.border = BORDER
            if isinstance(v, int):
                c.number_format = "#,##0"
        r += 1
    for col in ("E", "F", "G", "H", "I"):
        ws.column_dimensions[col].width = 11
    ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=9)
    c = ws.cell(row=r, column=2, value=(
        "Los totales por archivo suman más que el total final: las tablas, relaciones y canvases que aparecen "
        "en más de un archivo se unificaron en un solo objeto (hoja «Tablas unificadas»)."))
    c.font = DESC_FONT
    c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.row_dimensions[r].height = 26
    r += 2

    ws.cell(row=r, column=2, value="Estado final en la plataforma (tras la corrección)").font = \
        Font(bold=True, size=12, color=AZUL)
    r += 1
    for nombre, val in [
        ("Proyectos", t["proyectos"]), ("Carpetas", t["carpetas"]), ("Canvases", t["canvases"]),
        ("Tablas", t["tablas"]), ("Columnas", t["columnas"]), ("Vistas", t["vistas"]),
        ("Relaciones vigentes", t["relaciones_activas"]),
        ("— de subcategoría (supertipo→subtipo)", t["subcategorias"]),
        ("Relaciones desactivadas por la corrección", t["relaciones_desactivadas_fix"]),
        ("Columnas retiradas por la unificación", t["columnas_retiradas"]),
        ("Relaciones marcadas en ROJO (llave incompleta)", t["relaciones_rojas_vigentes"]),
    ]:
        ws.cell(row=r, column=2, value=nombre).font = Font(size=10)
        c = ws.cell(row=r, column=3, value=val)
        c.number_format = "#,##0"
        c.font = Font(size=10, bold=True)
        r += 1
    r += 1

    ws.cell(row=r, column=2, value="Índice de hojas").font = Font(bold=True, size=12, color=AZUL)
    r += 1
    vn = D["_vistas_nums"]
    nd = D["_no_definido_nums"]
    INDICE = [
        ("Tablas renombradas _DUPn", len(D["renombradas"]),
         "Tablas con nombre físico repetido: la copia más usada conservó el nombre y estas copias se "
         "renombraron con sufijo _DUPn. Indica en qué canvas quedó cada copia y cuál conservó el nombre."),
        ("Tablas unificadas", len(D["unificadas"]),
         "La misma tabla venía en más de un archivo XML y se unificó en una sola. Indica qué versión quedó "
         "(la más usada) y dónde está."),
        ("Columnas retiradas", len(D["cols_retiradas"]),
         "Columnas que solo existían en la versión perdedora de una tabla unificada: ya no están en la plataforma."),
        ("Relaciones desactivadas", len(D["desactivadas"]),
         "Relaciones que usaban una columna retirada; la corrección las desactivó. En Erwin se ven; en la "
         "plataforma ya no. Requieren decisión caso por caso."),
        ("Relaciones en rojo", len(D["llave_incompleta"]),
         "Relaciones vigentes cuyas columnas no cubren la llave primaria completa del padre (así vienen en "
         "Erwin). La plataforma las pinta de ROJO en el canvas."),
        ("Relaciones no migradas", len(D["no_migradas"]),
         "Relaciones rotas de origen o que no unen columna con columna: no existen en la plataforma. "
         "Al comparar contra Erwin, estas líneas solo se verán en Erwin."),
        ("Columnas duplicadas", len(D["cols_duplicadas"]),
         "Columnas que venían repetidas e idénticas dentro de una misma tabla: quedó una sola."),
        ("Particiones reasignadas", len(D["particiones"]),
         "Tablas con correlativos de partición PART_nn duplicados o en desorden: el particionado se "
         "reasignó según el orden físico de las columnas."),
        ("Vistas", len(D["vistas"]),
         f"Vistas descartadas, duplicadas ({vn['dup_copias']} copias), multi-fuente sin join "
         f"({vn['multi']}) y columnas de vista sin vínculo a su origen ({vn['vcol']})."),
        ("Tablas sin canvas", len(D["sin_canvas"]),
         f"Tablas migradas que no aparecen dibujadas en ningún canvas "
         f"({len(D['sin_canvas']) - D['_sin_canvas_dibujadas']} tampoco lo estaban en Erwin; "
         f"{D['_sin_canvas_dibujadas']} perdieron su figura al unificarse canvases entre archivos)."),
        ("Canvases vacíos", len(D["canvases_vacios"]),
         "Diagramas que venían vacíos en Erwin; el canvas existe sin figuras."),
        ("Esquema no definido", len(D["no_definido"]),
         f"Tablas ({nd['tablas']}) y vistas ({nd['vistas']}) de los archivos DDV que venían sin esquema "
         f"(Hive_Database) y entraron a «No_Definido»."),
        ("UDP fuera de catálogo", len(D["udp_fuera_catalogo"]),
         "Valores UDP que difieren del catálogo solo en mayúsculas/minúsculas; se migraron tal cual. "
         "Pendiente normalizar."),
        ("UDP omitidas", len(D["udp_omitidas"]),
         "Definiciones UDP sin ningún uso en su archivo: no se migraron."),
    ]
    for j, h in enumerate(["Hoja", "Filas", "Contenido"], 2):
        c = ws.cell(row=r, column=j, value=h)
        c.font, c.fill, c.border = HDR_FONT, HDR_FILL, BORDER
    r += 1
    for nombre, n, txt in INDICE:
        c = ws.cell(row=r, column=2, value=nombre)
        c.hyperlink = f"#'{nombre}'!A1"
        c.font = Font(color="0563C1", underline="single", size=10)
        c.border = BORDER
        cn = ws.cell(row=r, column=3, value=n)
        cn.number_format = "#,##0"
        cn.border = BORDER
        cn.alignment = CENTER
        ct = ws.cell(row=r, column=4, value=txt)
        ct.alignment = Alignment(wrap_text=True, vertical="top")
        ct.font = Font(size=9)
        ct.border = BORDER
        ws.row_dimensions[r].height = 13 * (len(txt) // 100 + 1) + 5
        r += 1
    r += 1

    ws.cell(row=r, column=2, value="Notas de conciliación").font = Font(bold=True, size=12, color=AZUL)
    r += 1
    notas = [
        f"• Relaciones vigentes: {t['relaciones_activas'] + t['relaciones_desactivadas_fix']:,} migradas − "
        f"{t['relaciones_desactivadas_fix']} desactivadas por la corrección = {t['relaciones_activas']:,}.",
    ]
    if t["rojas_contando_pk_retiradas"] != t["relaciones_rojas_vigentes"]:
        notas.append(
            f"• Relaciones en rojo: contando también las columnas PK ya retiradas por la unificación darían "
            f"{t['rojas_contando_pk_retiradas']}; contra la llave vigente —lo que se ve en el canvas— son "
            f"{t['relaciones_rojas_vigentes']}.")
    if nd["vistas_descartadas"]:
        notas.append(
            f"• {nd['vistas_descartadas']} vistas sin esquema fueron además descartadas por no tener fuente "
            f"(hoja «Vistas») y no aparecen en «Esquema no definido».")
    for txt in notas:
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=4)
        c = ws.cell(row=r, column=2, value=txt)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        c.font = Font(size=9, color="555555")
        ws.row_dimensions[r].height = 13 * (len(txt) // 105 + 1) + 4
        r += 1
    ws.sheet_view.showGridLines = False

    # ── hojas de detalle ───────────────────────────────────────────────────
    add_sheet(
        "Tablas renombradas _DUPn", "tabla",
        "Tablas renombradas con sufijo _DUPn",
        "El nombre físico de tabla debe ser único en toda la plataforma. Cuando un nombre venía repetido en "
        "los XML, la copia MÁS USADA (más relaciones, vistas y diagramas que la referencian) conservó el nombre "
        "y las demás copias se renombraron con sufijo _DUPn, con todo su contenido intacto. Cada fila es una "
        "copia renombrada. Para validar: abrir el canvas indicado y comparar con el diagrama de Erwin — la "
        "tabla se verá con el nombre nuevo (_DUPn). Decisión pendiente por copia: eliminarla si es un "
        "duplicado real, o darle un nombre propio.",
        ["Archivo XML", "Proyecto", "Esquema", "Nombre original", "Nombre lógico",
         "Nombre final en la plataforma", "Copias con ese nombre", "Copia que conservó el nombre",
         "Canvases donde aparece esta copia (_DUPn)", "Canvases de la copia que conservó el nombre"],
        [12, 12, 26, 32, 32, 36, 9, 32, 52, 52],
        D["renombradas"],
        ["archivo", "proyecto", "esquema", "nombre_original", "nombre_logico",
         "nombre_final", "copias", "conservo_nombre", "canvases", "canvases_kept"],
        wrap_cols=(9, 10), num_cols=(7,))

    add_sheet(
        "Tablas unificadas", "tabla",
        "Tablas que venían en más de un archivo XML (unificadas en una sola)",
        "La misma tabla (mismo identificador de Erwin, o mismo esquema y nombre) existía en dos o más "
        "archivos. No se duplicó: quedó una sola. Cuando las versiones diferían, ganó la MÁS USADA («usos» = "
        "relaciones, vistas y diagramas que la referencian). Si ganó la versión ya cargada, el archivo "
        "posterior se enganchó a ella; sus columnas sin equivalente no entraron. Si ganó el archivo posterior, "
        "la tabla se actualizó y las columnas que solo estaban en la versión anterior se retiraron "
        "(hoja «Columnas retiradas»).",
        ["Archivo XML (aparición posterior)", "Proyecto", "Esquema", "Tabla", "Nombre lógico",
         "Usos en el archivo", "Usos en la BD", "Resultado", "Columnas del archivo sin equivalente",
         "Archivos donde aparece", "Canvases donde aparece"],
        [14, 12, 24, 34, 32, 10, 10, 44, 12, 20, 52],
        D["unificadas"],
        ["archivo", "proyecto", "esquema", "tabla", "nombre_logico", "usos_archivo", "usos_bd",
         "resultado", "cols_sin_equivalente", "archivos", "canvases"],
        wrap_cols=(8, 11), num_cols=(6, 7, 9))

    add_sheet(
        "Columnas retiradas", "tabla",
        "Columnas retiradas al unificar tablas entre archivos",
        "Al unificar una tabla que venía en dos archivos, ganó una de las versiones. Estas columnas solo "
        "existían en la versión perdedora y se retiraron de la plataforma: la tabla se ve SIN ellas en el "
        "canvas, aunque en el archivo Erwin perdedor sí aparecen. Si una columna era parte de una relación, "
        "esa relación quedó desactivada (hoja «Relaciones desactivadas»).",
        ["Proyecto", "Esquema", "Tabla", "Columna retirada", "Nombre lógico", "¿Era PK?",
         "Venía del archivo", "¿Afectó una relación?", "Canvases de la tabla"],
        [12, 22, 36, 32, 32, 8, 14, 30, 52],
        D["cols_retiradas"],
        ["proyecto", "esquema", "tabla", "columna", "columna_logica", "era_pk",
         "venia_de", "afecta_relacion", "canvases"],
        wrap_cols=(9,), center_cols=(6,))

    add_sheet(
        "Relaciones desactivadas", "relacion",
        "Relaciones desactivadas por la corrección",
        "Estas relaciones usaban una columna que solo existía en la versión perdedora de una tabla unificada. "
        "Al retirarse la columna quedaron colgando, y la corrección (audit_data_consistency --fix) las "
        "desactivó. En el canvas de la plataforma YA NO se dibujan; en el diagrama de Erwin SÍ aparecen. "
        "Decisión por caso: si la relación debe existir, volver a crear la columna y redibujar la relación; "
        "si la versión vigente de la tabla es la correcta, se queda desactivada. La columna «¿Existe otra "
        "relación vigente?» marca los casos ya cubiertos por una relación gemela sana.",
        ["Relación en Erwin", "Archivo XML", "Proyecto", "Tabla padre", "Tabla hija",
         "Columnas del vínculo (padre → hija)", "Tipo",
         "¿Existe otra relación vigente entre las mismas tablas?", "Canvases donde se dibujaba"],
        [13, 12, 12, 36, 40, 52, 14, 16, 52],
        D["desactivadas"],
        ["relacion_erwin", "archivo", "proyecto", "tabla_padre", "tabla_hija", "pares",
         "tipo", "gemela", "canvases"],
        wrap_cols=(6, 9), center_cols=(8,))

    add_sheet(
        "Relaciones en rojo", "relacion",
        "Relaciones vigentes con la llave incompleta (se pintan de ROJO en el canvas)",
        "Las columnas que conectan a la tabla hija no cubren la llave primaria completa de la tabla padre. "
        "Así viene la data en Erwin — no es un efecto de la migración. La plataforma las marca en ROJO; el "
        "popup de la relación muestra qué columnas de la PK faltan y permite completarlas (Sync). Si el padre "
        "no tiene PK, todos los pares aparecen como «fuera de la llave» — delata la llave faltante. Ordenadas "
        "por cantidad de canvases donde se ven, para priorizar la revisión.",
        ["Relación en Erwin", "Archivo XML", "Proyecto", "Tabla padre", "Tabla hija", "¿Subcategoría?",
         "Columnas conectadas (padre → hija)", "Columnas de la PK del padre que FALTAN",
         "Columnas conectadas que NO son PK", "N.° de canvases", "Canvases donde se ve"],
        [13, 12, 12, 34, 38, 10, 46, 30, 28, 9, 52],
        D["llave_incompleta"],
        ["relacion_erwin", "archivo", "proyecto", "tabla_padre", "tabla_hija", "subcategoria",
         "pares", "pk_faltante", "extra_fuera_pk", "n_canvases", "canvases"],
        wrap_cols=(7, 8, 9, 11), num_cols=(10,), center_cols=(6,))

    add_sheet(
        "Relaciones no migradas", "relacion",
        "Relaciones que NO existen en la plataforma",
        "Dos motivos. (1) Rotas de origen: apuntan a una tabla o vista que no existe en el propio XML — "
        "venían así de Erwin. (2) Sin pares de columnas: la relación no une ninguna columna del padre con "
        "ninguna del hijo, y la plataforma exige columna origen y destino. Al comparar un canvas contra su "
        "diagrama de Erwin, estas líneas se verán SOLO en Erwin. La columna «Dónde verla en Erwin» indica "
        "carpeta (subject area) y diagrama; el canvas homónimo de la plataforma es el mismo, solo que sin "
        "esa línea.",
        ["Archivo XML", "Proyecto", "Relación en Erwin", "Tipo", "Tabla padre", "Lógico (padre)",
         "Tabla hija", "Lógico (hija)", "Estado", "Motivo", "Dónde verla en Erwin (Carpeta / Diagrama)"],
        [12, 12, 13, 22, 34, 26, 34, 26, 15, 40, 52],
        D["no_migradas"],
        ["archivo", "proyecto", "relacion_erwin", "tipo", "tabla_padre", "padre_logico",
         "tabla_hija", "hija_logico", "estado", "motivo", "donde_erwin"],
        wrap_cols=(10, 11))

    add_sheet(
        "Columnas duplicadas", "tabla",
        "Columnas duplicadas descartadas dentro de una misma tabla",
        "La tabla traía la misma columna dos o más veces, idéntica (mismo nombre, tipo y definición). Se "
        "conservó una sola copia por tabla, priorizando la que participa en relaciones, luego la PK, la que "
        "tiene definición, dominio y valores UDP — por eso este descarte no rompió ninguna relación. En el "
        "canvas la tabla se ve con la columna una sola vez; en Erwin puede verse repetida.",
        ["Archivo XML", "Proyecto", "Esquema", "Tabla", "Columna descartada", "Canvases de la tabla"],
        [12, 12, 26, 38, 30, 56],
        D["cols_duplicadas"],
        ["archivo", "proyecto", "esquema", "tabla", "columna", "canvases"],
        wrap_cols=(6,))

    add_sheet(
        "Particiones reasignadas", "tabla",
        "Tablas con el correlativo de partición reasignado",
        "Los correlativos de partición (PART_01, PART_02, …) venían duplicados o en desorden en el XML. "
        "Regla aplicada: manda el ORDEN FÍSICO de las columnas en la tabla, no el correlativo. La columna "
        "«Correlativos leídos» muestra los números tal como venían, recorriendo las columnas en su orden "
        "físico; en la plataforma quedaron renumerados 1, 2, 3… en ese mismo orden. El particionado del "
        "DDL export queda consistente.",
        ["Archivo XML", "Proyecto", "Esquema", "Tabla", "Correlativos leídos (en orden físico)",
         "Canvases de la tabla"],
        [12, 12, 26, 40, 22, 56],
        D["particiones"],
        ["archivo", "proyecto", "esquema", "tabla", "correlativos", "canvases"],
        wrap_cols=(6,), center_cols=(5,))

    add_sheet(
        "Vistas", "vista",
        "Vistas con observaciones",
        "Cuatro casos. Descartadas: ninguna de sus fuentes se pudo resolver a una tabla física — no existen "
        "en la plataforma. Duplicadas: mismo esquema y nombre repetido en el archivo — quedó una sola (la más "
        "usada). Multi-fuente sin join: la vista tiene 2 o más tablas fuente sin join declarado ni relación "
        "entre ellas — pendiente declarar el join en el editor de vistas. Columna de vista sin origen: la "
        "columna no pudo vincularse a su columna de la tabla fuente y se migró con definición propia.",
        ["Caso", "Archivo XML", "Proyecto", "Esquema", "Vista", "Detalle", "Canvases / dónde verla"],
        [26, 12, 12, 28, 44, 52, 52],
        D["vistas"],
        ["caso", "archivo", "proyecto", "esquema", "vista", "detalle", "canvases"],
        wrap_cols=(6, 7))

    add_sheet(
        "Tablas sin canvas", "otro",
        "Tablas migradas que no aparecen en ningún canvas",
        "Estas tablas existen en la plataforma (se encuentran por el buscador y el explorador de BD) pero no "
        "están dibujadas en ningún canvas. La columna «Situación» distingue las que tampoco estaban dibujadas "
        "en Erwin de las que perdieron su figura al unificarse canvases entre archivos (mismo diagrama Erwin "
        "en dos archivos: quedaron las figuras del último archivo cargado).",
        ["Proyecto", "Archivo XML", "Esquema", "Tabla", "Nombre lógico", "Situación"],
        [12, 20, 22, 40, 36, 70],
        D["sin_canvas"],
        ["proyecto", "archivo", "esquema", "tabla", "nombre_logico", "nota"],
        wrap_cols=(6,))

    add_sheet(
        "Canvases vacíos", "otro",
        "Canvases sin ninguna figura",
        "Diagramas que venían vacíos en Erwin y se crearon igual como canvases vacíos. Decisión pendiente: "
        "dibujarlos o eliminarlos. La columna «Situación» avisa si el diagrama homónimo del otro archivo sí "
        "dibujaba tablas (canvases unificados).",
        ["Proyecto", "Canvas (ruta completa)", "Situación"],
        [12, 60, 90],
        D["canvases_vacios"],
        ["proyecto", "canvas", "nota"],
        wrap_cols=(3,))

    add_sheet(
        "Esquema no definido", "otro",
        "Tablas y vistas de los archivos DDV sin esquema (entraron a «No_Definido»)",
        "Estos objetos no traían Hive_Database (esquema) en el XML y entraron al esquema «No_Definido». "
        "A diferencia del UDV —donde no manejar esquema es lo esperado y por eso no se lista—, en el DDV "
        "probablemente sí deberían tener uno. Decisión pendiente: asignarles su esquema real desde Properties.",
        ["Tipo", "Proyecto", "Nombre", "Nombre lógico", "Canvases donde aparece"],
        [8, 12, 44, 36, 60],
        D["no_definido"],
        ["tipo", "proyecto", "nombre", "nombre_logico", "canvases"],
        wrap_cols=(5,), center_cols=(1,))

    add_sheet(
        "UDP fuera de catálogo", "otro",
        "Valores UDP escritos distinto al catálogo de su definición",
        "El valor guardado difiere del catálogo solo en mayúsculas/minúsculas (inconsistencia de tipeo en "
        "Erwin). Se migraron tal cual para no perder información. Decisión pendiente: normalizar estos "
        "valores a la forma exacta del catálogo (columna «Valor del catálogo»), o ampliar el catálogo si se "
        "prefiere aceptar las variantes.",
        ["Nivel", "Proyecto", "Objeto", "UDP", "Valor guardado", "Valor del catálogo",
         "Canvases donde aparece"],
        [10, 12, 52, 22, 16, 16, 52],
        D["udp_fuera_catalogo"],
        ["nivel", "proyecto", "entidad", "udp", "valor", "valor_catalogo", "canvases"],
        wrap_cols=(3, 7), center_cols=(1, 5, 6))

    add_sheet(
        "UDP omitidas", "otro",
        "Definiciones UDP que no se migraron",
        "Definiciones de propiedades UDP sin ningún uso observado en su archivo: no aportaban información y "
        "no se migraron. Aparte de estas, tampoco se migraron las definiciones de ruido técnico que Erwin "
        "genera por cada símbolo de subcategoría, ni las de niveles que la plataforma no modela (View, "
        "Key_Group, Relationship).",
        ["Definición UDP", "Nivel", "Tipo de dato", "Archivos donde venía", "Motivo"],
        [44, 12, 12, 30, 50],
        D["udp_omitidas"],
        ["udp", "nivel", "tipo", "archivos", "nota"],
        wrap_cols=(5,), center_cols=(2, 3))

    wb.save(out_path)
    log(f"OK → {out_path} ({os.path.getsize(out_path) // 1024} KB, {len(wb.sheetnames)} hojas)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--reports-dir", default=os.path.join(BACKEND, "migration-reports"))
    ap.add_argument("--xml-root", default=os.path.join(BACKEND, "..", "folder_data"))
    ap.add_argument("--out", default=os.path.join(BACKEND, "..", "REPORTE-MIGRACION-ERWIN-DETALLE.xlsx"))
    ap.add_argument("--cache-dir", default=None,
                    help="Carpeta para cachear los XML parseados (pickle) entre corridas")
    args = ap.parse_args(argv)

    files = discover(args.reports_dir, args.xml_root)
    log("Archivos de la última corrida:")
    for fi in files:
        log(f"  · {fi['tag']}: {fi['xml_path']}")
    parse_models(files, args.cache_dir)
    data = extract(files)
    build_excel(data, os.path.abspath(args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
