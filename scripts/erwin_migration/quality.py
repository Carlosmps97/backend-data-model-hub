"""Gate de calidad PRE-migración sobre exports .xml de Erwin (no toca la BD).

Reporta incongruencias del archivo contra las reglas de la plataforma
(doc 10) y las políticas de migración (doc 12 §8), indicando qué haría la
migración con cada una:

  ERROR — requiere decisión/corrección manual (la migración lo omite o corta).
  WARN  — la migración lo resuelve sola con la política aprobada.
  INFO  — pérdida/particularidad aceptada; solo para conocimiento.

Uso:
  .venv/bin/python -m scripts.erwin_migration.quality "ruta/modelo.xml" [más.xml ...]
      [--json salida.json]   volcado completo para automatizar
Exit code 1 si hay ERRORs (gate para CI / corridas encadenadas).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter, defaultdict

from . import erwin_parser as ep
from . import policies as pol


def _finding(findings, code, severity, title, action, items):
    findings.append({
        "code": code, "severity": severity, "title": title,
        "action": action, "count": len(items), "samples": items[:8],
        "items": items,
    })


def analyze(m: ep.ErwinModel) -> list[dict]:
    f: list[dict] = []
    schema_of = m.owner_schema()
    attr_idx = m.attr_index()
    fk_pairs = m.fk_pairs()
    view_rels = m.view_source_rels()

    # ── Tablas ────────────────────────────────────────────────────────────
    no_schema = [f"{e.physical} ({e.name})" for e in m.entities.values()
                 if e.id not in schema_of]
    _finding(f, "W-NO-SCHEMA-TABLE", "WARN",
             "Tablas sin Hive_Database (schema)",
             f'se migran con schema "{pol.NO_SCHEMA}"', no_schema)

    macros = [f"{e.name} → usa '{e.physical}'" for e in m.entities.values()
              if e.physical_was_macro]
    _finding(f, "W-MACRO-PHYSNAME", "WARN",
             "Physical_Name con macro Erwin sin resolver (%…)",
             "se usa User_Formatted_Physical_Name", macros)

    # duplicados de tabla por (schema efectivo + nombre físico) — política
    # multi-archivo doc 32b R3 (owner 2026-07-24): score de uso + alias.
    by_key: dict[str, list[str]] = defaultdict(list)
    for e in m.entities.values():
        key = f"{pol.schema_or_default(schema_of.get(e.id))}.{e.physical}".upper()
        by_key[key].append(e.name)
    dup_tables = [f"{k} ×{len(v)}" for k, v in by_key.items() if len(v) > 1]
    _finding(f, "W-DUP-TABLE", "WARN",
             "Tablas duplicadas por schema+nombre físico",
             "se migran TODAS (política 2026-08-22): la MÁS USADA conserva el "
             "nombre y las demás llevan sufijo _DUPn con su contenido intacto "
             "— el mapeo exacto sale en el reporte de migración",
             dup_tables)

    # regla de plataforma doc 50: el físico de tabla es único GLOBAL (el
    # esquema NO participa). Homónimos en esquemas DISTINTOS no los toca R3:
    # entrarían como docs separados y quedan en grandfather — visibilizar.
    schemas_of_phys: dict[str, set[str]] = defaultdict(set)
    for e in m.entities.values():
        if not e.physical:      # sin físico resoluble no hay clave que chocar
            continue
        schemas_of_phys[e.physical.upper()].add(
            pol.schema_or_default(schema_of.get(e.id)).upper())
    dup_global = [f"{phys} en {len(ss)} esquemas: {', '.join(sorted(ss))}"
                  for phys, ss in sorted(schemas_of_phys.items()) if len(ss) > 1]
    _finding(f, "W-DUP-TABLE-GLOBAL", "WARN",
             "Nombre físico repetido en esquemas DISTINTOS (regla global doc 50)",
             "la migración conserva UNO y renombra el resto con sufijo _DUPn "
             "(política 2026-08-22) para respetar la unicidad global",
             dup_global)

    # doc 53 §2.1: en modelos SOLO lógicos el equipo repite nombres de entidad
    # (el físico deriva del lógico vía macro) → esas copias caen en el colapso
    # R3 aunque sean entidades distintas a propósito. Aviso previo pedido.
    by_logical: dict[str, list[str]] = defaultdict(list)
    for e in m.entities.values():
        key = (e.name or "").strip().lower()
        if key:
            by_logical[key].append(e.name)
    dup_logical = [f"{names[0]} ×{len(names)}"
                   for _k, names in sorted(by_logical.items()) if len(names) > 1]
    _finding(f, "W-DUP-LOGICAL-NAME", "WARN",
             "Entidades con el MISMO nombre lógico (case-insensitive)",
             "la plataforma no exige unicidad del lógico; si el físico deriva "
             "del lógico (macro %EntityName), los homónimos físicos se migran "
             "TODOS con sufijo _DUPn (política 2026-08-22) — revisar con el "
             "equipo cuáles son duplicados reales", dup_logical)

    # columnas duplicadas dentro del mismo objeto
    dup_cols = []
    for coll, kind in ((m.entities, "tabla"), (m.views, "vista")):
        for obj in coll.values():
            _, dropped = pol.dedupe_columns(obj.attributes)
            for a in dropped:
                dup_cols.append(f"{kind} {getattr(obj, 'physical', obj.name)}.{a.physical}")
    _finding(f, "W-DUP-COLUMN", "WARN",
             "Columnas duplicadas dentro de una tabla/vista (regla #9)",
             "se conserva la 1ª (orden físico) y se descartan las demás",
             dup_cols)

    empty_types = [f"{e.physical}.{a.physical}" for e in m.entities.values()
                   for a in e.attributes if not a.data_type]
    _finding(f, "W-EMPTY-DATATYPE", "WARN",
             "Columnas sin tipo de dato físico",
             'se migran como "STRING"', empty_types)

    pk_nullable = [f"{e.physical}.{a.physical}" for e in m.entities.values()
                   for a in e.attributes if a.id in e.pk_attr_ids and a.nullable]
    _finding(f, "I-PK-NULLABLE", "INFO",
             "Columnas PK marcadas nullable en Erwin (inconsistencia del origen)",
             "se migran tal cual (isPrimaryKey=True, isNullable=True)",
             pk_nullable)

    # ── Vistas ────────────────────────────────────────────────────────────
    no_source = [v.name for v in m.views.values()
                 if not any(r.parent_ref in m.entities for r in view_rels.get(v.id, []))]
    _finding(f, "W-VIEW-NO-SOURCE", "WARN",
             "Vistas sin relación de derivación válida (sin tabla fuente)",
             "se DESCARTAN (política aprobada 2026-07-24, doc 32b R7)",
             no_source)

    vcol_no_origin = [f"{v.name}.{a.physical}" for v in m.views.values()
                      for a in v.attributes
                      if not a.parent_attr_ref or a.parent_attr_ref not in attr_idx]
    _finding(f, "W-VIEWCOL-NO-ORIGIN", "WARN",
             "Columnas de vista sin columna origen resoluble",
             "se migran como passthrough por nombre (sin mapping de origen)",
             vcol_no_origin)

    no_schema_v = [v.name for v in m.views.values() if v.id not in schema_of]
    _finding(f, "W-NO-SCHEMA-VIEW", "WARN",
             "Vistas sin Hive_Database (schema)",
             f'se migran con schema "{pol.NO_SCHEMA}"', no_schema_v)

    dup_views: dict[str, int] = Counter(
        f"{pol.schema_or_default(schema_of.get(v.id))}.{v.name}".upper()
        for v in m.views.values())
    _finding(f, "W-DUP-VIEW", "WARN",
             "Vistas duplicadas por schema+nombre",
             "gana la copia más usada/completa (doc 32b R3); las demás quedan "
             "como alias", [f"{k} ×{n}" for k, n in dup_views.items() if n > 1])

    # ── Relaciones ────────────────────────────────────────────────────────
    broken_rels, no_pairs = [], []
    for r in m.relationships.values():
        if r.rel_type == ep.REL_TABLE_TO_VIEW:
            if r.parent_ref not in m.entities or r.child_ref not in m.views:
                broken_rels.append(f"{r.name} (tabla→vista con extremo inexistente)")
            continue
        if r.rel_type not in (ep.REL_IDENTIFYING, ep.REL_NON_IDENTIFYING,
                              ep.REL_SUBTYPE):
            continue
        if r.parent_ref not in m.entities or r.child_ref not in m.entities:
            broken_rels.append(f"{r.name} (extremo inexistente)")
            continue
        pairs = [(p, c) for p, c in fk_pairs.get(r.id, [])
                 if p in attr_idx and c in attr_idx]
        if not pairs:
            no_pairs.append(f"{r.name} ({m.entities[r.parent_ref].physical} → "
                            f"{m.entities[r.child_ref].physical})")
    _finding(f, "E-REL-BROKEN", "ERROR",
             "Relaciones con tabla/vista inexistente",
             "la migración las OMITE — confirmar", broken_rels)
    _finding(f, "W-REL-NO-PAIRS", "WARN",
             "Relaciones tabla-tabla sin pares de columnas FK resolubles",
             "se OMITEN (la plataforma exige columna origen y destino)",
             no_pairs)

    # ── Subcategorías (doc 53) ───────────────────────────────────────────
    sym_of = m.subtype_symbol_of_rel()
    orphan_sub = []
    for r in m.relationships.values():
        if r.rel_type == ep.REL_SUBTYPE and r.id not in sym_of:
            pn = m.entities[r.parent_ref].physical if r.parent_ref in m.entities else "?"
            cn = m.entities[r.child_ref].physical if r.child_ref in m.entities else "?"
            orphan_sub.append(f"{r.name} ({pn} → {cn})")
    _finding(f, "W-SUBTYPE-ORPHAN-REL", "WARN",
             "Relaciones de subtipo (Type 9) sin Subtype_Symbol que las agrupe",
             "se migran con un símbolo sintético propio (grupo de 1)",
             orphan_sub)

    child_groups: dict[str, set[str]] = defaultdict(set)
    for s in m.subtype_symbols.values():
        for rid in s.rel_refs:
            r = m.relationships.get(rid)
            if r is not None and r.rel_type == ep.REL_SUBTYPE:
                child_groups[r.child_ref].add(s.id)
    multi_child = [
        f"{m.entities[c].physical if c in m.entities else c} en {len(g)} grupos"
        for c, g in child_groups.items() if len(g) > 1]
    _finding(f, "W-SUBTYPE-CHILD-MULTI", "WARN",
             "Entidades que son subtipo en MÁS de un grupo de subcategoría",
             "Erwin no lo permite por defecto — revisar el modelo origen "
             "(la migración las carga tal cual)", multi_child)

    # ── Dominios / UDP / Glosario ────────────────────────────────────────
    dead_domain = [f"{e.physical}.{a.physical}" for e in m.entities.values()
                   for a in e.attributes
                   if a.domain_ref and a.domain_ref not in m.domains]
    _finding(f, "W-DOMAIN-DEAD", "WARN",
             "Columnas con Parent_Domain_Ref a dominio inexistente",
             "se migran con parentDomainId=None", dead_domain)

    with_parent = [d.name for d in m.domains.values()
                   if not d.builtin and d.parent_ref
                   and d.parent_ref in m.domains
                   and not m.domains[d.parent_ref].builtin]
    _finding(f, "I-DOMAIN-HIERARCHY", "INFO",
             "Dominios con dominio padre (jerarquía Erwin)",
             "la plataforma no tiene jerarquía de dominios: se APLANA",
             with_parent)

    known_defs = set(m.udp_defs)
    dead_udp = Counter(def_id for _o, _t, def_id, _v in m.udp_values
                       if def_id not in known_defs)
    _finding(f, "W-UDP-DEAD-DEF", "WARN",
             "Valores UDP cuya definición no existe en el archivo",
             "se OMITEN", [f"{k} ×{n}" for k, n in dead_udp.items()])

    unsupported = sorted({d.full_name for d in m.udp_defs.values()
                          if d.owner_class not in pol.UDP_LEVEL_MAP})
    _finding(f, "I-UDP-LEVEL-UNSUPPORTED", "INFO",
             "Defs UDP de niveles sin equivalente (Key_Group/Relationship/…)",
             "se OMITEN (decisión doc 12)", unsupported)

    terms = Counter(g[0].strip().upper() for g in m.glossary if g)
    _finding(f, "E-GLOSSARY-DUP-TERM", "ERROR",
             "Términos de glosario exactamente duplicados",
             "la migración conserva el 1º — confirmar",
             [f"{k} ×{n}" for k, n in terms.items() if n > 1])

    by_abbrev: dict[str, set[str]] = defaultdict(set)
    for g in m.glossary:
        if len(g) > 1 and g[1]:
            by_abbrev[g[1].upper()].add(g[0])
    _finding(f, "I-GLOSSARY-SHARED-ABBREV", "INFO",
             "Abreviaturas compartidas por 2+ términos (válido: Renovado/Renovada)",
             "se migran todas",
             [f"{k}: {sorted(v)}" for k, v in by_abbrev.items() if len(v) > 1])

    # ── Canvases ─────────────────────────────────────────────────────────
    known_objs = set(m.entities) | set(m.views)
    rel_ids = set(m.relationships)
    sym_ids = set(m.subtype_symbols)   # doc 53: el círculo se deriva en canvas
    rel_shapes = sum(1 for d in m.diagrams for ref, _a in d.shapes if ref in rel_ids)
    dead_shapes = [f"{d.name}: {ref[:24]}…" for d in m.diagrams
                   for ref, _a in d.shapes
                   if ref and ref not in known_objs and ref not in rel_ids
                   and ref not in sym_ids]
    _finding(f, "I-SHAPE-RELATIONSHIP", "INFO",
             "Shapes de línea de relación en diagramas (esperado)",
             "el canvas de la plataforma deriva los wires de `relationships`",
             [f"{rel_shapes} líneas de relación"] if rel_shapes else [])
    _finding(f, "I-SHAPE-DEAD", "INFO",
             "Shapes de diagrama que no apuntan a tabla/vista/relación",
             "se OMITEN del canvas", dead_shapes)

    on_diagram = {ref for d in m.diagrams for ref, _a in d.shapes}
    orphan_tables = [e.physical for e in m.entities.values() if e.id not in on_diagram]
    _finding(f, "I-TABLE-NO-DIAGRAM", "INFO",
             "Tablas fuera de todo diagrama",
             "quedan en el catálogo (sin canvas)", orphan_tables)

    empty_diagrams = [f"{d.subject_area}/{d.name}" for d in m.diagrams if not d.shapes]
    _finding(f, "I-DIAGRAM-EMPTY", "INFO",
             "Diagramas sin shapes", "se crean como canvas vacíos", empty_diagrams)

    # ── Partición (política v2, doc 32b R6) ──────────────────────────────
    udp_vals = pol.resolve_udp_values(m.udp_values, pol.udp_defs_by_view(m.udp_defs))
    reassign = []
    for e in m.entities.values():
        vals = [(a.id, corr) for a in e.attributes
                if (corr := pol.partition_correlative(
                    udp_vals.get((a.id, pol.PARTITION_UDP_KEY)))) is not None]
        if not vals:
            continue
        _ids, motivo = pol.partition_marks(vals)
        if motivo:
            reassign.append(f"{e.physical}: {motivo}")
    _finding(f, "I-PARTITION-REASSIGN", "INFO",
             "Particiones PART_nn con correlativo duplicado o en desorden",
             "se marcan igual; el orden EFECTIVO es el físico (política "
             "2026-07-24, doc 32b R6) — quedan en el reporte de migración",
             reassign)

    # ── Pérdidas aceptadas por decisión ──────────────────────────────────
    n_indexes = sum(e.index_key_groups for e in m.entities.values())
    _finding(f, "I-INDEXES-SKIPPED", "INFO",
             "Índices (Key_Group IF*) — la plataforma no modela índices",
             "NO se migran (decisión pendiente de feature web)",
             [f"{n_indexes} índices en total"] if n_indexes else [])
    _finding(f, "I-ANNOTATIONS-DISCARDED", "INFO",
             "Anotaciones de diagrama", "se DESCARTAN (decisión owner)",
             [f"{m.annotations} anotaciones"] if m.annotations else [])
    _finding(f, "I-SUBTYPE-UDP-NOISE", "INFO",
             "Defs UDP de Subtype_Symbol (ruido de símbolos Erwin)",
             "se OMITEN", ([f"{m.subtype_udp_defs} defs"]
                           if m.subtype_udp_defs else []))
    return [x for x in f if x["count"] > 0]


def glossary_cross_conflicts(files: list[tuple[str, list[tuple[str, ...]]]]) -> list[dict]:
    """Full outer join del glosario ENTRE archivos (política 2026-08-22): que
    un término exista solo en un archivo NO es problema (la unión los suma);
    la incongruencia real es el MISMO término con abreviatura DISTINTA en
    archivos distintos (p. ej. "monto" = MTO vs MTOS). Dentro de cada archivo
    manda la 1ª aparición (misma regla que la migración; los duplicados
    internos ya los cubre E-GLOSSARY-DUP-TERM). Comparación case-insensitive.
    Devuelve [{term, byFile: {archivo: abbrev}}]. Puro."""
    by_term: dict[str, dict[str, str]] = {}
    display: dict[str, str] = {}
    for fname, glossary in files:
        seen_local: set[str] = set()
        for g in glossary:
            term = (g[0] or "").strip()
            if not term:
                continue
            key = term.upper()
            if key in seen_local:
                continue
            seen_local.add(key)
            display.setdefault(key, term)
            by_term.setdefault(key, {})[fname] = (g[1].strip() if len(g) > 1 else "")
    out: list[dict] = []
    for key in sorted(by_term):
        entry = by_term[key]
        distinct = {a.upper() for a in entry.values() if a}
        if len(entry) > 1 and len(distinct) > 1:
            out.append({"term": display[key], "byFile": entry})
    return out


def summarize(m: ep.ErwinModel) -> dict:
    schema_of = m.owner_schema()
    return {
        "modelo": m.name,
        "subject_areas": len(m.subject_areas),
        "diagramas": len(m.diagrams),
        "tablas": len(m.entities),
        "columnas_tabla": sum(len(e.attributes) for e in m.entities.values()),
        "vistas": len(m.views),
        "columnas_vista": sum(len(v.attributes) for v in m.views.values()),
        "relaciones_tabla_tabla": sum(1 for r in m.relationships.values()
                                      if r.rel_type in (ep.REL_IDENTIFYING,
                                                        ep.REL_NON_IDENTIFYING)),
        "relaciones_subtipo": sum(1 for r in m.relationships.values()
                                  if r.rel_type == ep.REL_SUBTYPE),
        "simbolos_subcategoria": len(m.subtype_symbols),
        "derivaciones_tabla_vista": sum(1 for r in m.relationships.values()
                                        if r.rel_type == ep.REL_TABLE_TO_VIEW),
        "dominios_custom": sum(1 for d in m.domains.values() if not d.builtin),
        "defs_udp_migrables": len(pol.udp_defs_by_view(m.udp_defs)),
        "valores_udp_explicitos": len(m.udp_values),
        "terminos_glosario": len(m.glossary),
        "schemas_con_objetos": len({v for v in schema_of.values()}),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Gate de calidad sobre exports .xml de Erwin (solo lectura)")
    ap.add_argument("xml", nargs="+", help="ruta(s) al .xml exportado de Erwin")
    ap.add_argument("--json", dest="json_out", help="volcar reporte completo a JSON")
    ap.add_argument("--brief", action="store_true",
                    help="vista compacta (8 muestras por finding). Por DEFECTO "
                         "se imprime TODO lo encontrado (política 2026-08-22: "
                         "la validación es transparente, sin resumir)")
    args = ap.parse_args(argv)

    all_reports = []
    glossaries: list[tuple[str, list[tuple[str, ...]]]] = []
    worst = 0
    for path in args.xml:
        print(f"\n{'=' * 72}\nARCHIVO: {path}")
        m = ep.parse(path)
        glossaries.append((path, m.glossary))
        s = summarize(m)
        print(f"Modelo: {s['modelo']}")
        print("  " + " · ".join(f"{k}={v}" for k, v in s.items() if k != "modelo"))
        findings = analyze(m)
        errors = [x for x in findings if x["severity"] == "ERROR"]
        warns = [x for x in findings if x["severity"] == "WARN"]
        infos = [x for x in findings if x["severity"] == "INFO"]
        for group, tag in ((errors, "ERROR"), (warns, "WARN"), (infos, "INFO")):
            for x in group:
                print(f"\n[{tag}] {x['code']} — {x['title']}  ({x['count']})")
                print(f"        acción: {x['action']}")
                shown = x["samples"] if args.brief else x["items"]
                for s_ in shown:
                    print(f"        · {s_}")
                if x["count"] > len(shown):
                    print(f"        · … y {x['count'] - len(shown)} más (--brief off = todo)")
        print(f"\nRESULTADO: {len(errors)} ERROR / {len(warns)} WARN / {len(infos)} INFO"
              + ("  →  APTO PARA MIGRAR (revisar WARN/INFO)" if not errors
                 else "  →  REQUIERE DECISIÓN ANTES DE MIGRAR"))
        all_reports.append({"file": path, "summary": s, "findings": findings})
        worst = max(worst, 1 if errors else 0)

    # ── Glosario ENTRE archivos (full outer join, política 2026-08-22) ──────
    if len(glossaries) > 1:
        conflicts = glossary_cross_conflicts(glossaries)
        print(f"\n{'=' * 72}\nGLOSARIO ENTRE ARCHIVOS (full outer join)")
        if conflicts:
            print(f"[ERROR] E-GLOSSARY-XFILE-CONFLICT — mismo término con "
                  f"abreviatura DISTINTA entre archivos  ({len(conflicts)})")
            print("        acción: decidir la abreviatura correcta ANTES de "
                  "migrar (la migración conserva la vigente y reporta la otra)")
            for c in conflicts:
                detail = " · ".join(f"{os.path.basename(f)} → {a or '(vacía)'}"
                                    for f, a in c["byFile"].items())
                print(f"        · {c['term']}: {detail}")
            worst = 1
        else:
            print("  Sin incongruencias: los términos compartidos usan la misma "
                  "abreviatura; los exclusivos de cada archivo se suman sin conflicto.")
        all_reports.append({"crossFile": {"glossaryConflicts": conflicts}})

    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as fh:
            json.dump(all_reports, fh, ensure_ascii=False, indent=1)
        print(f"\nReporte JSON: {args.json_out}")
    return worst


if __name__ == "__main__":
    sys.exit(main())
