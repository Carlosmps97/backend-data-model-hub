"""Extrae el MODELO DE DATOS limpio de un XML nativo de Erwin (Save As XML):
tablas + columnas, vistas con sus orígenes, relaciones FK y canvases
(diagramas) agrupados por subject area.

Es el complemento de `extract_standards.py` (glosario/dominios/UDP): solo
LECTURA del XML → archivos JSON (y CSV opcional). No toca la base de datos y
NO obfusca nombres — es el extracto fiel, con las mismas políticas neutras de
la migración (policies.py): esquema faltante → "No_Definido" y columnas
duplicadas dentro de una tabla → se queda la 1ª.

De dónde sale cada cosa dentro del XML (ver erwin_parser.py):
  - Tablas/columnas ...... objetos `<Entity>` con sus `<Attribute>` (nombre
                           lógico + físico, tipo, nullable, PK por Key_Group,
                           FK por parent_attr_ref, dominio por
                           Parent_Domain_Ref, UDPs por UDP_Instance).
  - Esquema .............. `<Hive_Database>` (miembros por referencia).
  - Vistas ............... objetos `<View>`; cada columna apunta a su columna
                           origen (passthrough) → tabla fuente.
  - Relaciones ........... `<Relationship>` tipo 2 (identifying) y 7
                           (non-identifying) + pares de columnas FK.
  - Canvases ............. `<ER_Diagram>` (shapes → tablas/vistas miembro),
                           agrupados por `<Subject_Area>`.

Uso (desde la raíz del backend, con el venv del proyecto — solo stdlib):
  .venv/bin/python -m scripts.erwin_migration.extract_model \
      "../folder_data/DDV - CPYBCA.xml" [--out DIR] [--csv]

Salida (en --out, default `model_out/<nombre-del-xml>/`):
  tables.json         [{schema, physicalName, logicalName, definition,
                        udpValues, columns:[{physicalName, logicalName,
                        dataType, nullable, isPrimaryKey, isForeignKey,
                        domain, definition, udpValues}]}]
  views.json          [{schema, name, definition, sql, columns:[{outputAlias,
                        dataType, sourceTable, sourceColumn}]}]
  relationships.json  [{name, type, parentTable, childTable,
                        columnPairs:[{parentColumn, childColumn}]}]
  canvases.json       [{name, subjectArea, tables:[...], views:[...]}]
  subject_areas.json  [{name, definition, canvases:[...]}]
  summary.json        conteos + duplicados descartados
Con --csv además: tables.csv, columns.csv (plano: 1 fila por columna),
views.csv (1 fila por columna de vista), relationships.csv (1 fila por par).
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from . import erwin_parser as ep
from . import policies as pol


def _qual(schema: str, name: str) -> str:
    return f"{schema}.{name}" if schema else name


def extract(model: ep.ErwinModel) -> tuple[dict[str, list[dict]], int]:
    """Modelo limpio del XML parseado (puro, testeable). Devuelve
    (secciones, nº de columnas duplicadas descartadas)."""
    schema_of = model.owner_schema()
    collapsed = pol.collapse_udp_defs(model.udp_defs)
    udp_vals = pol.resolve_udp_values(model.udp_values, collapsed)
    dom_name = {d.id: d.name for d in model.domains.values()}

    def udps_for(oid: str, level: str) -> dict[str, str]:
        return {e["name"]: udp_vals[(oid, key)]
                for key, e in collapsed.items()
                if e["level"] == level and (oid, key) in udp_vals}

    def schema_for(oid: str) -> str:
        return pol.schema_or_default(schema_of.get(oid))

    # attr id → (entidad dueña, attr): para resolver orígenes de vistas y FKs.
    ent_of_attr = {a.id: (e, a) for e in model.entities.values()
                   for a in e.attributes}

    tables, dropped_total = [], 0
    for e in model.entities.values():
        cols, dropped = pol.dedupe_columns(e.attributes)
        dropped_total += len(dropped)
        pk_pos = {aid: i for i, aid in enumerate(e.pk_attr_order)}
        tables.append({
            "schema": schema_for(e.id),
            "physicalName": e.physical, "logicalName": e.name,
            "definition": e.definition or None,
            "udpValues": udps_for(e.id, "table"),
            "columns": [{
                "physicalName": a.physical, "logicalName": a.name,
                "dataType": a.data_type or None, "nullable": a.nullable,
                "isPrimaryKey": a.id in e.pk_attr_ids,
                "pkPosition": pk_pos.get(a.id),
                "isForeignKey": bool(a.parent_attr_ref),
                "domain": dom_name.get(a.domain_ref or "") or None,
                "definition": a.definition or a.comment or None,
                "udpValues": udps_for(a.id, "column"),
            } for a in cols],
        })
    tables.sort(key=lambda t: (t["schema"], t["physicalName"]))

    views = []
    for v in model.views.values():
        rows = []
        for a in v.attributes:
            src = ent_of_attr.get(a.parent_attr_ref or "")
            rows.append({
                "outputAlias": a.physical, "dataType": a.data_type or None,
                "sourceTable": _qual(schema_for(src[0].id), src[0].physical) if src else None,
                "sourceColumn": src[1].physical if src else None,
            })
        views.append({"schema": schema_for(v.id), "name": v.name,
                      "definition": v.definition or None,
                      "sql": v.sql or None, "columns": rows})
    views.sort(key=lambda w: (w["schema"], w["name"]))

    pairs = model.fk_pairs()
    attr_idx = model.attr_index()
    rels = []
    for r in model.relationships.values():
        if r.rel_type not in (ep.REL_IDENTIFYING, ep.REL_NON_IDENTIFYING):
            continue  # tipo 16 (tabla→vista) ya está expresado en views.json
        parent = model.entities.get(r.parent_ref)
        child = model.entities.get(r.child_ref)
        if not parent or not child:
            continue
        rels.append({
            "name": r.name or None,
            "type": "identifying" if r.rel_type == ep.REL_IDENTIFYING else "non-identifying",
            "parentTable": _qual(schema_for(parent.id), parent.physical),
            "childTable": _qual(schema_for(child.id), child.physical),
            "columnPairs": [
                {"parentColumn": attr_idx[p].physical, "childColumn": attr_idx[c].physical}
                for (p, c) in pairs.get(r.id, []) if p in attr_idx and c in attr_idx],
        })
    rels.sort(key=lambda r: (r["parentTable"], r["childTable"]))

    canvases = []
    for d in model.diagrams:
        t_refs = [ref for (ref, _a) in d.shapes if ref in model.entities]
        v_refs = [ref for (ref, _a) in d.shapes if ref in model.views]
        canvases.append({
            "name": d.name, "subjectArea": d.subject_area,
            "tables": sorted(_qual(schema_for(t), model.entities[t].physical) for t in t_refs),
            "views": sorted(_qual(schema_for(v), model.views[v].name) for v in v_refs),
        })
    canvases.sort(key=lambda c: (c["subjectArea"], c["name"]))

    subject_areas = [
        {"name": s["name"], "definition": s["definition"] or None,
         "canvases": [c["name"] for c in canvases if c["subjectArea"] == s["name"]]}
        for s in sorted(model.subject_areas, key=lambda s: s["order"])]

    return {"tables": tables, "views": views, "relationships": rels,
            "canvases": canvases, "subject_areas": subject_areas}, dropped_total


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    keys = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8-sig") as f:  # BOM → Excel
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("; ".join(str(x) for x in v) if isinstance(v, list)
                            else json.dumps(v, ensure_ascii=False) if isinstance(v, dict)
                            else v)
                        for k, v in r.items()})


def _flat_csvs(out: Path, data: dict[str, list[dict]]) -> None:
    """Vistas planas para Excel: 1 fila por columna / par FK."""
    _write_csv(out / "tables.csv", [
        {"schema": t["schema"], "physicalName": t["physicalName"],
         "logicalName": t["logicalName"], "columns": len(t["columns"]),
         "definition": t["definition"]} for t in data["tables"]])
    _write_csv(out / "columns.csv", [
        {"schema": t["schema"], "table": t["physicalName"], **{
            k: c[k] for k in ("physicalName", "logicalName", "dataType",
                              "nullable", "isPrimaryKey", "isForeignKey", "domain")}}
        for t in data["tables"] for c in t["columns"]])
    _write_csv(out / "views.csv", [
        {"schema": v["schema"], "view": v["name"], **c}
        for v in data["views"] for c in v["columns"]])
    _write_csv(out / "relationships.csv", [
        {"type": r["type"], "parentTable": r["parentTable"],
         "childTable": r["childTable"], **p}
        for r in data["relationships"]
        for p in (r["columnPairs"] or [{"parentColumn": None, "childColumn": None}])])


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("xml", help="ruta al export Erwin (Save As XML)")
    ap.add_argument("--out", default=None,
                    help="directorio de salida (default: model_out/<xml>)")
    ap.add_argument("--csv", action="store_true",
                    help="además de JSON, emite CSVs planos abribles en Excel")
    args = ap.parse_args()

    xml = Path(args.xml)
    if not xml.is_file():
        raise SystemExit(f"no existe: {xml}")
    out = Path(args.out) if args.out else Path("model_out") / xml.stem
    out.mkdir(parents=True, exist_ok=True)

    print(f"[1/3] parseando {xml.name}…")
    model = ep.parse(str(xml))
    print(f"      modelo: {model.name!r}")

    print("[2/3] extrayendo modelo…")
    data, dropped = extract(model)

    print(f"[3/3] escribiendo en {out}/")
    for name, rows in data.items():
        (out / f"{name}.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.csv:
        _flat_csvs(out, data)
    n_cols = sum(len(t["columns"]) for t in data["tables"])
    summary = {"model": model.name,
               "tables": len(data["tables"]), "columns": n_cols,
               "columns_duplicadas_descartadas": dropped,
               "views": len(data["views"]),
               "relationships": len(data["relationships"]),
               "canvases": len(data["canvases"]),
               "subject_areas": len(data["subject_areas"])}
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"OK · tablas={summary['tables']} · columnas={n_cols} "
          f"(dup descartadas {dropped}) · vistas={summary['views']} · "
          f"relaciones={summary['relationships']} · canvases={summary['canvases']}")


if __name__ == "__main__":
    main()
