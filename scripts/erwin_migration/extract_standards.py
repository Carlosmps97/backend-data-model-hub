"""Extrae los ESTÁNDARES limpios de un XML nativo de Erwin (Save As XML):
glosario (NSM), parent domains y definiciones UDP con sus valores permitidos.

Es solo LECTURA del XML → archivos JSON (y CSV opcional). No toca la base de
datos, no obfusca nombres, no depende de la plataforma: es el extracto fiel
de las secciones de estándares del modelo.

De dónde sale cada cosa dentro del XML (ver erwin_parser.py):
  - Parent domains ....... objetos `<Domain>` (sección propia del modelo).
                           Se excluyen los built-in de Erwin y los `<default>`.
  - Glosario ............. `<Glossary_Word_List>` (el Naming Standard NSM
                           embebido), campos separados por 0x1F.
  - Defs UDP ............. objetos `<Property_Type>` (sección propia), nombre
                           `OwnerClass.ViewMode.Nombre`; se COLAPSAN las
                           variantes Logical/Physical de una misma def y el
                           owner se mapea a nivel: Entity→table,
                           Attribute→column, Model→canvas (policies.py).
  - allowedValues ........ para UDP tipo list: el default de la def ∪ los
                           VALORES OBSERVADOS en el modelo (los `UDP_Instance`
                           que cuelgan de cada tabla/columna/modelo) — la def
                           de Erwin no trae el catálogo completo de valores.

Uso (desde la raíz del backend, con el venv del proyecto — solo stdlib):
  .venv/bin/python -m scripts.erwin_migration.extract_standards \
      "../folder_data/DDV - CPYBCA.xml" [--out DIR] [--csv]

Salida (en --out, default `standards_out/<nombre-del-xml>/`):
  glossary.json         [{term, abbrev, alts}]
  parent_domains.json   [{name, dataType, definition}]
  udp_definitions.json  [{name, level, dataType, default, allowedValues,
                          usedBy: {nº objetos con valor}}]
  summary.json          conteos + nombre del modelo
"""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path

from . import erwin_parser as ep
from . import policies as pol


def extract(model: ep.ErwinModel) -> dict[str, list[dict]]:
    """Estándares limpios del modelo parseado (puro, testeable)."""
    # Parent domains: sección <Domain>, sin built-ins ni placeholders "<...>".
    domains = [
        {"name": d.name, "dataType": d.data_type or "STRING",
         "definition": d.definition or None}
        for d in model.domains.values()
        if not d.builtin and not d.name.startswith("<")
    ]

    # Glosario NSM: (término, abreviatura, *alternativas), dedup por término.
    seen: set[str] = set()
    glossary: list[dict] = []
    for g in model.glossary:
        term = g[0].strip()
        if not term or term.upper() in seen:
            continue
        seen.add(term.upper())
        glossary.append({"term": term,
                         "abbrev": g[1].strip() if len(g) > 1 else "",
                         "alts": [x for x in g[2:] if x]})

    # Defs UDP colapsadas (Logical/Physical → una) + valores observados.
    # Doc 69: una def por faceta (`level|view|name`), sin colapsar Logical/Physical.
    defs = pol.udp_defs_by_view(model.udp_defs)
    udp_vals = pol.resolve_udp_values(model.udp_values, defs)
    values_by_key: dict[str, set] = defaultdict(set)
    used_by: dict[str, int] = defaultdict(int)
    for (_oid, key), val in udp_vals.items():
        values_by_key[key].add(val)
        used_by[key] += 1
    udps = [
        {"name": e["name"], "level": e["level"], "view": e["view"], "dataType": e["dataType"],
         "default": e["default"] or None,
         "allowedValues": (sorted(values_by_key.get(key, set())
                                  | ({e["default"]} if e["default"] else set()))
                           if e["dataType"] == "list" else []),
         "usedBy": used_by.get(key, 0)}
        for key, e in sorted(defs.items())
    ]
    return {"parent_domains": domains, "glossary": glossary,
            "udp_definitions": udps}


def _write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    keys = list(rows[0].keys())
    with path.open("w", newline="", encoding="utf-8-sig") as f:  # BOM → Excel
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            w.writerow({k: ("; ".join(v) if isinstance(v, list) else v)
                        for k, v in r.items()})


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("xml", help="ruta al export Erwin (Save As XML)")
    ap.add_argument("--out", default=None,
                    help="directorio de salida (default: standards_out/<xml>)")
    ap.add_argument("--csv", action="store_true",
                    help="además de JSON, emite CSVs abribles en Excel")
    args = ap.parse_args()

    xml = Path(args.xml)
    if not xml.is_file():
        raise SystemExit(f"no existe: {xml}")
    out = Path(args.out) if args.out else Path("standards_out") / xml.stem
    out.mkdir(parents=True, exist_ok=True)

    print(f"[1/3] parseando {xml.name}…")
    model = ep.parse(str(xml))
    print(f"      modelo: {model.name!r}")

    print("[2/3] extrayendo estándares…")
    data = extract(model)

    print(f"[3/3] escribiendo en {out}/")
    for name, rows in data.items():
        (out / f"{name}.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        if args.csv:
            _write_csv(out / f"{name}.csv", rows)
    summary = {"model": model.name,
               **{k: len(v) for k, v in data.items()},
               "udp_values_observados": len(model.udp_values)}
    (out / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"OK · dominios={summary['parent_domains']} · "
          f"glosario={summary['glossary']} · udp={summary['udp_definitions']}")


if __name__ == "__main__":
    main()
