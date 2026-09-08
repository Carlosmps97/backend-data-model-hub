"""Orquestador de la migración Erwin (doc 54): ONE-SHOT por carpeta y APPEND.

Dos modos mutuamente excluyentes:

ONE-SHOT (destructivo) — deja la plataforma como un primer deployment:
  .venv/bin/python -m scripts.run_migration --folder "ruta/carpeta"          # dry-run
  .venv/bin/python -m scripts.run_migration --folder "ruta/carpeta" --apply [--force]
  Descubre TODOS los .xml (recursivo, orden determinista por subruta) y corre:
  quality POR PROYECTO (gate + glosario cruzado sólo entre los archivos que
  se unen en un mismo proyecto) → reset DESTRUCTIVO → create_admin (4 roles
  de caja + admin/admin) → migrate archivo por archivo → audit →
  seed_ddl_export_rules ("data functions", en todos los proyectos) →
  arrange_all → mark_base_version (v1 de cada proyecto, título parametrizable).

APPEND (no destructivo) — suma UN archivo sobre la base viva:
  .venv/bin/python -m scripts.run_migration --append "ruta/modelo.xml" --apply
  quality → crosscheck (vs BD) → migrate → audit → arrange del proyecto.
  Sin reset, sin admin, sin seeds, sin marcador (v1 ya existe).

Proyecto destino por archivo (doc 75 D9): lo decide el MANIFIESTO
`<carpeta>/projects.json` — `{"projects": [{"name", "files": [patrones
relativos a la carpeta], "description"?}]}` — y, para los archivos que ningún
patrón matchea, la regla general: el nombre del archivo sin extensión (un XML =
un proyecto). Sólo el manifiesto une varios XML en un proyecto (p. ej. los DDV
en «Modelo DDV»); dos archivos que caerían al mismo nombre sin estar unidos
por el manifiesto son un error, no un merge silencioso. El manifiesto se
valida contra los archivos reales ANTES de tocar la BD. Del `<Locator>` del
XML sólo se conservan dominio y modelo (capas del Mart) como origen
informativo. `--project` (solo --append) fuerza un destino explícito.

El apply es SECUENCIAL a propósito (no configurable): la unicidad de nombres
físicos POR PROYECTO (política `_DUPn`) depende del prefetch que cada archivo
hace contra la BD al empezar; en paralelo se correría con fotos desactualizadas.

Manejo de fallas por paso:
  - quality (gate)        → detiene TODO antes de borrar nada (salvo --force)
  - reset / create_admin  → detienen el resto (base a medio wipe: no seguir)
  - migrate / audit / seeds / mark_base → la falla se registra, se continúa
    con el resto y el exit code final es 1 (falla VISIBLE, nunca silenciosa)
  - crosscheck / arrange  → informativos: warning, no afectan el exit code

Los pasos invocan los MISMOS scripts del repo (cero lógica duplicada), cada
uno como subproceso propio (memoria liberada entre parses de cientos de MB)
con la salida streameada COMPLETA — nada se resume ni se oculta.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import unescape

from scripts.erwin_migration.policies import parse_mart_locator as parse_locator

ROOT = Path(__file__).resolve().parent.parent
_PY = sys.executable

_LOCATOR_RE = re.compile(rb"<Locator>([^<]{1,600})</Locator>")
_PEEK_CHUNK = 8 * 1024 * 1024
_PEEK_CAP = 64 * 1024 * 1024

MANIFEST_NAME = "projects.json"
_MANIFEST_KEYS = {"name", "files", "description"}


class ManifestError(ValueError):
    """Manifiesto inválido o inconsistente con los archivos: se aborta ANTES de
    tocar la BD (un typo en un patrón convertiría los 31 DDV en 31 proyectos)."""


# ── Descubrimiento y proyecto destino (lógica pura, testeable) ───────────────

def discover_xmls(root: Path) -> list[Path]:
    """Todos los .xml bajo `root` (recursivo), orden determinista por subruta."""
    found = {str(p): p for pat in ("*.xml", "*.XML")
             for p in root.rglob(pat) if p.is_file()}
    return sorted(found.values(),
                  key=lambda p: str(p.relative_to(root)).lower())


def read_locator(path: Path, cap: int = _PEEK_CAP,
                 _chunk: int = _PEEK_CHUNK) -> str | None:
    """Primer `<Locator>` del XML sin parsear el archivo entero (escaneo por
    chunks con tope). Los modelos guardados en Mart lo traen cerca del inicio."""
    tail = b""
    read = 0
    with open(path, "rb") as fh:
        while read < cap:
            chunk = fh.read(min(_chunk, cap - read))
            if not chunk:
                break
            read += len(chunk)
            m = _LOCATOR_RE.search(tail + chunk)
            if m:
                return unescape(m.group(1).decode("utf-8", "replace"))
            tail = (tail + chunk)[-700:]
    return None


def _glob_re(pattern: str) -> re.Pattern:
    """Glob relativo a la raíz: `*`/`?` no cruzan `/`, `**/` sí; case-insensitive."""
    out, i = [], 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$", re.IGNORECASE)


def load_manifest(path: Path | None) -> dict:
    """`{"projects": [{name, files[], description?}]}` (doc 75 D9). Sin archivo →
    sin entradas: cada XML es un proyecto con el nombre del archivo. Estricto:
    claves desconocidas, `name` vacío o repetido, `files` vacío → ManifestError."""
    if path is None or not path.is_file():
        return {"projects": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ManifestError(f"{path}: JSON inválido ({exc})") from exc
    if (not isinstance(data, dict) or set(data) != {"projects"}
            or not isinstance(data["projects"], list)):
        raise ManifestError(f'{path}: se espera {{"projects": [...]}}')
    seen: set[str] = set()
    for i, entry in enumerate(data["projects"]):
        if not isinstance(entry, dict):
            raise ManifestError(f"{path}: entrada {i} no es un objeto")
        extra = set(entry) - _MANIFEST_KEYS
        if extra:
            raise ManifestError(f"{path}: entrada {i}: claves desconocidas {sorted(extra)}")
        name = str(entry.get("name") or "").strip()
        if not name:
            raise ManifestError(f"{path}: entrada {i}: 'name' vacío")
        if name.lower() in seen:
            raise ManifestError(f"{path}: nombre de proyecto repetido «{name}»")
        seen.add(name.lower())
        files = entry.get("files")
        if (not isinstance(files, list) or not files
                or not all(isinstance(f, str) and f.strip() for f in files)):
            raise ManifestError(f"{path}: «{name}»: 'files' debe ser una lista de patrones no vacía")
    return data


def plan_files(files: list[Path], root: Path, manifest: dict,
               locator_of=read_locator) -> list[dict]:
    """Proyecto destino por archivo: la entrada del manifiesto que lo matchea o,
    si ninguna, el nombre del archivo (doc 75 D9). Dominio y modelo salen del
    Locator del Mart (informativos). Valida el manifiesto contra los archivos
    REALES: cualquier ambigüedad → ManifestError. `locator_of` es inyectable."""
    rels = {f: str(f.relative_to(root)).replace(os.sep, "/") for f in files}
    assigned: dict[Path, dict] = {}
    for entry in manifest.get("projects", []):
        name = entry["name"].strip()
        for pattern in entry["files"]:
            rx = _glob_re(pattern.strip())
            hits = [f for f in files if rx.match(rels[f])]
            if not hits:
                raise ManifestError(f"«{name}»: el patrón {pattern!r} no matchea ningún .xml bajo {root}")
            for f in hits:
                prev = assigned.get(f)
                if prev is not None and prev["name"].strip() != name:
                    raise ManifestError(f"{rels[f]}: matchea dos entradas del manifiesto "
                                        f"(«{prev['name'].strip()}» y «{name}»)")
                assigned[f] = entry
    plans = []
    for f in files:
        loc = locator_of(f)
        parsed = parse_locator(loc) if loc else None
        entry = assigned.get(f)
        plans.append({
            "path": f, "rel": rels[f],
            "project": entry["name"].strip() if entry else f.stem.strip(),
            "description": ((entry.get("description") or "").strip() or None) if entry else None,
            "domain": parsed["domain"] if parsed else "",
            "model": parsed["model"] if parsed else f.stem.strip(),
            "source": "manifest" if entry else "archivo",
        })
    by_name: dict[str, list[dict]] = {}
    for p in plans:
        by_name.setdefault(p["project"].lower(), []).append(p)
    for group in by_name.values():
        if len(group) > 1 and any(p["source"] == "archivo" for p in group):
            raise ManifestError(f"nombre de proyecto repetido «{group[0]['project']}»: "
                                + ", ".join(p["rel"] for p in group)
                                + " — declara la unión en el manifiesto o renombra el archivo")
    return plans


def group_by_project(plans: list[dict]) -> list[tuple[str, list[dict]]]:
    """[(proyecto, [planes])] en orden de primera aparición."""
    groups: dict[str, list[dict]] = {}
    for p in plans:
        groups.setdefault(p["project"], []).append(p)
    return list(groups.items())


# ── Pasos del pipeline (construcción pura, testeable) ────────────────────────
# klass: gate  = detiene todo salvo --force (corre ANTES de borrar nada)
#        abort = su falla detiene el resto del pipeline
#        core  = su falla marca el run como fallido, pero se continúa
#        check = validación: igual que core (falla visible, no detiene)
#        info  = informativo: warning, no afecta el exit code

def _quality_cmd(xmls: list[str], quality_json: str | None) -> list[str]:
    """`--json` va ANTES de los archivos: los .xml siguen siendo los últimos args."""
    extra = ["--json", quality_json] if quality_json else []
    return [_PY, "-m", "scripts.erwin_migration.quality", *extra, *xmls]


def _migrate_cmd(p: dict, force: bool) -> list[str]:
    cmd = [_PY, "-m", "scripts.erwin_migration.migrate", str(p["path"]),
           "--project", p["project"]]
    if p.get("description"):
        cmd += ["--description", p["description"]]
    cmd.append("--apply")
    if force:
        cmd.append("--force")
    return cmd


def _quality_json_of(quality_json: str | None, n: int) -> str | None:
    """Un reporte JSON del gate por proyecto: `<base>-<n>.json`."""
    return f"{quality_json[:-5]}-{n}.json" if quality_json else None


def oneshot_steps(plans: list[dict], *, force: bool, base_title: str,
                  quality_json: str | None = None) -> list[dict]:
    steps = []
    for n, (project, group) in enumerate(group_by_project(plans), start=1):
        steps.append({"name": f"quality «{project}» (gate + glosario cruzado del proyecto)",
                      "klass": "gate",
                      "cmd": _quality_cmd([str(p["path"]) for p in group],
                                          _quality_json_of(quality_json, n))})
    steps += [
        {"name": "reset DESTRUCTIVO (borra todo el schema)", "klass": "abort",
         "cmd": [_PY, "-m", "scripts.reset_for_migration", "--apply"]},
        {"name": "create_admin (4 roles de caja + admin/admin)", "klass": "abort",
         "cmd": [_PY, "scripts/create_admin.py"]},
    ]
    for p in plans:
        steps.append({"name": f"migrate {p['rel']} → «{p['project']}»",
                      "klass": "core", "cmd": _migrate_cmd(p, force)})
    steps += [
        {"name": "audit_data_consistency", "klass": "check",
         "cmd": [_PY, "-m", "scripts.audit_data_consistency"]},
        {"name": "seed_ddl_export_rules (data functions, por proyecto)", "klass": "core",
         "cmd": [_PY, "-m", "scripts.seed_ddl_export_rules", "--all-projects", "--apply"]},
        {"name": "arrange_all (layout ELK, todos los canvases)", "klass": "info",
         "cmd": [_PY, "scripts/arrange_all.py"]},
        {"name": "mark_base_version (v1 de cada proyecto)", "klass": "core",
         "cmd": [_PY, "-m", "scripts.mark_base_version", "--apply",
                 "--title", base_title]},
    ]
    return steps


def append_steps(plan: dict, *, force: bool, quality_json: str | None = None) -> list[dict]:
    xml = str(plan["path"])
    return [
        {"name": "quality (gate)", "klass": "gate",
         "cmd": _quality_cmd([xml], quality_json)},
        {"name": f"crosscheck vs BD viva del proyecto «{plan['project']}»", "klass": "info",
         "cmd": [_PY, "-m", "scripts.erwin_migration.crosscheck",
                 "--project", plan["project"], xml]},
        {"name": f"migrate {plan['rel']} → «{plan['project']}»", "klass": "core",
         "cmd": _migrate_cmd(plan, force)},
        {"name": "audit_data_consistency", "klass": "check",
         "cmd": [_PY, "-m", "scripts.audit_data_consistency"]},
        {"name": f"arrange_all del proyecto «{plan['project']}»", "klass": "info",
         "cmd": [_PY, "scripts/arrange_all.py", "--project", plan["project"]]},
    ]


# ── Ejecución ────────────────────────────────────────────────────────────────

def _run_step(step: dict) -> tuple[int, float]:
    print(f"\n{'━' * 72}\n▶ {step['name']}\n  $ {' '.join(step['cmd'])}\n",
          flush=True)
    t0 = time.perf_counter()
    p = subprocess.run(step["cmd"], cwd=ROOT)
    return p.returncode, time.perf_counter() - t0


def execute(steps: list[dict], *, force: bool) -> list[dict]:
    """Corre los pasos en orden con la política de fallas del docstring."""
    results = []
    abort = False
    for st in steps:
        if abort:
            results.append({**st, "rc": None, "secs": 0.0,
                            "estado": "OMITIDO", "fatal": False})
            continue
        rc, secs = _run_step(st)
        estado, fatal = "OK", False
        if rc != 0:
            if st["klass"] == "gate":
                if force:
                    estado = "CON ERRORS (--force: continúa)"
                else:
                    estado, fatal, abort = "ERROR — DETIENE (gate)", True, True
            elif st["klass"] == "abort":
                estado, fatal, abort = "ERROR — DETIENE", True, True
            elif st["klass"] in ("core", "check"):
                estado, fatal = "ERROR", True
            else:
                estado = "WARNING (informativo)"
        results.append({**st, "rc": rc, "secs": secs,
                        "estado": estado, "fatal": fatal})
    return results


def gate_error_summary(report: list[dict], max_items: int = 5) -> list[str]:
    """Líneas legibles con los ERROR del reporte JSON de `quality` (por archivo +
    glosario cruzado): qué detuvo el gate, sin releer 4 600 líneas de salida.
    Puro (testeable)."""
    out: list[str] = []
    for entry in report:
        if "findings" in entry:
            base = os.path.basename(str(entry.get("file") or ""))
            for x in entry["findings"]:
                if x.get("severity") != "ERROR":
                    continue
                out.append(f"  [ERROR] {x['code']} — {x['title']}  ({x['count']}) · {base}")
                out.append(f"          acción: {x.get('action', '')}")
                items = list(x.get("items") or x.get("samples") or [])
                out += [f"          · {i}" for i in items[:max_items]]
                if len(items) > max_items:
                    out.append(f"          · … y {len(items) - max_items} más")
        conflicts = (entry.get("crossFile") or {}).get("glossaryConflicts") or []
        if conflicts:
            out.append(f"  [ERROR] E-GLOSSARY-XFILE-CONFLICT — mismo término con abreviatura "
                       f"distinta entre archivos  ({len(conflicts)})")
            out += [f"          · {c.get('term')}" for c in conflicts[:max_items]]
    return out


def _gate_reports(quality_json: str) -> list[str]:
    """Reportes JSON del gate de este run: el del append (`<base>.json`) o los
    de cada proyecto del one-shot (`<base>-<n>.json`), en orden."""
    return sorted(glob.glob(f"{quality_json[:-5]}-*.json")) + (
        [quality_json] if os.path.isfile(quality_json) else [])


def _print_gate_help(quality_json: str) -> None:
    """Tras un gate fallido SIN --force: qué falló y cómo seguir."""
    print(f"\n{'─' * 72}\nEl gate de calidad detuvo el run ANTES de borrar nada: la BD sigue intacta.")
    lines: list[str] = []
    for report in _gate_reports(quality_json):
        try:
            with open(report, encoding="utf-8") as fh:
                lines += gate_error_summary(json.load(fh))
        except (OSError, ValueError):
            continue
    if lines:
        print("Errores que exigen una decisión:")
        print("\n".join(lines))
    else:
        print("(no encontré el reporte JSON del gate — revisa la salida de quality arriba)")
    print("\nPara hacer borrón y cuenta nueva OMITIENDO esos objetos (migrate los salta y los reporta),\n"
          "repite el mismo comando agregando --force. Los ERROR son residuos del XML de Erwin\n"
          "(referencias a objetos que ya no existen en el archivo), no algo que se arregle en la plataforma.")


def _print_summary(results: list[dict]) -> None:
    print(f"\n{'═' * 72}\nRESUMEN DEL RUN")
    for r in results:
        secs = f"{r['secs']:7.1f}s" if r["rc"] is not None else "      —"
        print(f"  [{r['estado']:<32}] {secs}  {r['name']}")


def _write_summary(mode: str, plans: list[dict], results: list[dict],
                   started: str) -> str:
    out = os.path.join(str(ROOT), "migration-reports",
                       f"run_migration-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({
            "mode": mode, "startedAt": started,
            "finishedAt": datetime.now(timezone.utc).isoformat(),
            "files": [{"rel": p["rel"], "project": p["project"],
                       "description": p.get("description"),
                       "domain": p["domain"], "model": p["model"],
                       "source": p["source"]} for p in plans],
            "steps": [{"name": r["name"], "rc": r["rc"], "estado": r["estado"],
                       "secs": round(r["secs"], 1)} for r in results],
        }, fh, ensure_ascii=False, indent=1)
    return out


def _print_plan(title: str, plans: list[dict], steps: list[dict]) -> None:
    print(f"{'═' * 72}\n{title}\n{'═' * 72}")
    print(f"\nARCHIVOS ({len(plans)}) — proyecto destino por archivo "
          f"(manifest = {MANIFEST_NAME}; archivo = nombre del .xml):")
    for p in plans:
        mb = p["path"].stat().st_size / 1e6
        origin = f" · origen: {p['domain']} / {p['model']}" if p["domain"] else f" · origen: {p['model']}"
        print(f"  {p['rel']}  ({mb:,.0f} MB)")
        print(f"      → proyecto «{p['project']}» [{p['source']}]{origin}")
    groups = group_by_project(plans)
    print(f"\nPROYECTOS ({len(groups)}):")
    for project, group in groups:
        desc = next((g["description"] for g in group if g.get("description")), None)
        print(f"  «{project}»" + (f" — {desc}" if desc else ""))
        for g in group:
            print(f"      · {g['rel']}")
    print(f"\nPASOS ({len(steps)}):")
    for i, st in enumerate(steps, 1):
        print(f"  {i:2}. [{st['klass']:<5}] {st['name']}")
        print(f"        $ {' '.join(st['cmd'])}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Orquestador de migración Erwin: one-shot por carpeta "
                    "(destructivo) o append de un archivo (doc 54)")
    ap.add_argument("--folder", help="ONE-SHOT: carpeta con los .xml (recursivo); "
                                     "DESTRUCTIVO: borra TODO el schema antes de cargar")
    ap.add_argument("--append", help="APPEND: un .xml que se SUMA a la BD viva "
                                     "(no borra nada)")
    ap.add_argument("--apply", action="store_true",
                    help="ejecutar de verdad (sin esto: imprime el plan)")
    ap.add_argument("--force", action="store_true",
                    help="continuar aunque el gate de calidad tenga ERRORs "
                         "(migrate omite los objetos con error)")
    ap.add_argument("--project", help="(solo --append) proyecto destino explícito")
    ap.add_argument("--manifest", help=f"manifiesto de proyectos (default en --folder: "
                                       f"<carpeta>/{MANIFEST_NAME}; en --append sólo si se pasa)")
    ap.add_argument("--base-title", default="Base - Migración Erwin (XML)",
                    help="(one-shot) título del marcador de versión base v1")
    args = ap.parse_args(argv)

    if bool(args.folder) == bool(args.append):
        ap.error("exactamente uno: --folder (one-shot) o --append (un archivo)")
    if args.project and not args.append:
        ap.error("--project es solo para --append")

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")   # los subprocesos heredan os.environ

    started = datetime.now(timezone.utc).isoformat()
    # Reporte JSON del gate: con él, un run detenido explica al final QUÉ lo detuvo.
    os.makedirs(os.path.join(str(ROOT), "migration-reports"), exist_ok=True)
    quality_json = os.path.join(str(ROOT), "migration-reports",
                                f"quality-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json")

    if args.folder:
        root = Path(args.folder).expanduser()
        if not root.is_dir():
            print(f"⛔ No es una carpeta: {root}")
            return 2
        files = discover_xmls(root)
        if not files:
            print(f"⛔ No hay .xml bajo {root} (búsqueda recursiva).")
            return 2
        mpath = Path(args.manifest).expanduser() if args.manifest else root / MANIFEST_NAME
        if args.manifest and not mpath.is_file():
            print(f"⛔ No existe el manifiesto: {mpath}")
            return 2
        try:
            plans = plan_files(files, root, load_manifest(mpath))
        except ManifestError as exc:
            print(f"⛔ Manifiesto: {exc}")
            return 2
        steps = oneshot_steps(plans, force=args.force, base_title=args.base_title,
                              quality_json=quality_json)
        _print_plan("ONE-SHOT (DESTRUCTIVO) — plan", plans, steps)
        if not args.apply:
            print("\nConteos actuales de la BD (esto es lo que se borraría):")
            _run_step({"name": "reset (dry-run informativo)",
                       "cmd": [_PY, "-m", "scripts.reset_for_migration"]})
            print("\nDRY-RUN — nada ejecutado. Repite con --apply "
                  "(el gate de calidad corre PRIMERO: si falla, no se borra nada).")
            return 0
    else:
        xml = Path(args.append).expanduser()
        if not xml.is_file():
            print(f"⛔ No existe el archivo: {xml}")
            return 2
        mpath = Path(args.manifest).expanduser() if args.manifest else None
        if mpath is not None and not mpath.is_file():
            print(f"⛔ No existe el manifiesto: {mpath}")
            return 2
        try:
            plans = plan_files([xml], xml.parent, load_manifest(mpath))
        except ManifestError as exc:
            print(f"⛔ Manifiesto: {exc}")
            return 2
        if args.project:
            plans[0]["project"] = args.project.strip()
            plans[0]["source"] = "--project"
        steps = append_steps(plans[0], force=args.force, quality_json=quality_json)
        _print_plan("APPEND (no destructivo) — plan", plans, steps)
        if not args.apply:
            print("\nDRY-RUN — nada ejecutado. Repite con --apply.")
            return 0

    results = execute(steps, force=args.force)
    _print_summary(results)
    gate_failed = any(r["klass"] == "gate" and r.get("rc") not in (0, None) for r in results)
    if gate_failed and not args.force:
        _print_gate_help(quality_json)
    out = _write_summary("one-shot" if args.folder else "append",
                         plans, results, started)
    print(f"\nResumen del run: {out}")
    if any(r["fatal"] for r in results):
        print("RESULTADO: CON ERRORES (revisar arriba).")
        return 1
    print("RESULTADO: OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
