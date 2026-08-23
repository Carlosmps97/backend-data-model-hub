"""Orquestador de la migración Erwin (doc 54): ONE-SHOT por carpeta y APPEND.

Dos modos mutuamente excluyentes:

ONE-SHOT (destructivo) — deja la plataforma como un primer deployment:
  .venv/bin/python -m scripts.run_migration --folder "ruta/carpeta"          # dry-run
  .venv/bin/python -m scripts.run_migration --folder "ruta/carpeta" --apply [--force]
  Descubre TODOS los .xml (recursivo, orden determinista por subruta) y corre:
  quality de TODOS (gate + glosario cruzado entre archivos) → reset
  DESTRUCTIVO → create_admin (4 roles de caja + admin/admin) → migrate
  archivo por archivo → audit → seed_ddl_export_rules ("data functions") →
  arrange_all → mark_base_version (v1, título parametrizable).

APPEND (no destructivo) — suma UN archivo sobre la base viva:
  .venv/bin/python -m scripts.run_migration --append "ruta/modelo.xml" --apply
  quality → crosscheck (vs BD) → migrate → audit → arrange del proyecto.
  Sin reset, sin admin, sin seeds, sin marcador (v1 ya existe).

Proyecto destino por archivo (pedido owner 2026-08-22): se extrae del
`<Locator>` del propio XML — `erwin://Mart://Mart/<Proyecto>/<Dominio>/<Modelo>`,
las 3 capas del Mart de Erwin (proyecto / dominio / modelo). Archivos del
MISMO proyecto Mart comparten proyecto en la plataforma (familia, como el
DDV). Si el XML no trae Locator (modelo nunca guardado en Mart), fallback:
nombre del archivo sin extensión. `--project` (solo --append) fuerza un
destino explícito.

El apply es SECUENCIAL a propósito (no configurable): la unicidad global de
nombres físicos (política `_DUPn`) depende del prefetch que cada archivo hace
contra la BD al empezar; en paralelo se correría con fotos desactualizadas.

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


def plan_files(files: list[Path], root: Path, locator_of=read_locator) -> list[dict]:
    """Plan por archivo: proyecto destino (Locator del Mart o nombre del
    archivo), dominio y modelo. `locator_of` es inyectable para tests."""
    plans = []
    for f in files:
        loc = locator_of(f)
        parsed = parse_locator(loc) if loc else None
        if parsed:
            plans.append({"path": f, "rel": str(f.relative_to(root)),
                          "project": parsed["project"],
                          "domain": parsed["domain"],
                          "model": parsed["model"], "source": "locator"})
        else:
            plans.append({"path": f, "rel": str(f.relative_to(root)),
                          "project": f.stem.strip(), "domain": "",
                          "model": f.stem.strip(), "source": "archivo"})
    return plans


def filename_collisions(plans: list[dict]) -> dict[str, list[str]]:
    """Proyectos derivados del NOMBRE DE ARCHIVO que se repiten: esos archivos
    migrarían al MISMO proyecto (merge de familia). Se avisa, no se bloquea."""
    by: dict[str, list[str]] = {}
    for p in plans:
        if p["source"] == "archivo":
            by.setdefault(p["project"].lower(), []).append(p["rel"])
    return {k: v for k, v in by.items() if len(v) > 1}


# ── Pasos del pipeline (construcción pura, testeable) ────────────────────────
# klass: gate  = detiene todo salvo --force (corre ANTES de borrar nada)
#        abort = su falla detiene el resto del pipeline
#        core  = su falla marca el run como fallido, pero se continúa
#        check = validación: igual que core (falla visible, no detiene)
#        info  = informativo: warning, no afecta el exit code

def oneshot_steps(plans: list[dict], *, force: bool, base_title: str) -> list[dict]:
    xmls = [str(p["path"]) for p in plans]
    steps = [
        {"name": "quality (gate + glosario cruzado)", "klass": "gate",
         "cmd": [_PY, "-m", "scripts.erwin_migration.quality", *xmls]},
        {"name": "reset DESTRUCTIVO (borra todo el schema)", "klass": "abort",
         "cmd": [_PY, "-m", "scripts.reset_for_migration", "--apply"]},
        {"name": "create_admin (4 roles de caja + admin/admin)", "klass": "abort",
         "cmd": [_PY, "scripts/create_admin.py"]},
    ]
    for p in plans:
        cmd = [_PY, "-m", "scripts.erwin_migration.migrate", str(p["path"]),
               "--project", p["project"], "--apply"]
        if force:
            cmd.append("--force")
        steps.append({"name": f"migrate {p['rel']} → «{p['project']}»",
                      "klass": "core", "cmd": cmd})
    steps += [
        {"name": "audit_data_consistency", "klass": "check",
         "cmd": [_PY, "-m", "scripts.audit_data_consistency"]},
        {"name": "seed_ddl_export_rules (data functions)", "klass": "core",
         "cmd": [_PY, "-m", "scripts.seed_ddl_export_rules", "--apply"]},
        {"name": "arrange_all (layout ELK, todos los canvases)", "klass": "info",
         "cmd": [_PY, "scripts/arrange_all.py"]},
        {"name": "mark_base_version (v1)", "klass": "core",
         "cmd": [_PY, "-m", "scripts.mark_base_version", "--apply",
                 "--title", base_title]},
    ]
    return steps


def append_steps(plan: dict, *, force: bool) -> list[dict]:
    xml = str(plan["path"])
    mig = [_PY, "-m", "scripts.erwin_migration.migrate", xml,
           "--project", plan["project"], "--apply"]
    if force:
        mig.append("--force")
    return [
        {"name": "quality (gate)", "klass": "gate",
         "cmd": [_PY, "-m", "scripts.erwin_migration.quality", xml]},
        {"name": "crosscheck vs BD viva", "klass": "info",
         "cmd": [_PY, "-m", "scripts.erwin_migration.crosscheck", xml]},
        {"name": f"migrate {plan['rel']} → «{plan['project']}»", "klass": "core",
         "cmd": mig},
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
                       "domain": p["domain"], "model": p["model"],
                       "source": p["source"]} for p in plans],
            "steps": [{"name": r["name"], "rc": r["rc"], "estado": r["estado"],
                       "secs": round(r["secs"], 1)} for r in results],
        }, fh, ensure_ascii=False, indent=1)
    return out


def _print_plan(title: str, plans: list[dict], steps: list[dict]) -> None:
    print(f"{'═' * 72}\n{title}\n{'═' * 72}")
    print(f"\nARCHIVOS ({len(plans)}) — proyecto destino por archivo:")
    for p in plans:
        mb = p["path"].stat().st_size / 1e6
        dom = f" · dominio: {p['domain']}" if p["domain"] else ""
        print(f"  {p['rel']}  ({mb:,.0f} MB)")
        print(f"      → proyecto «{p['project']}» [{p['source']}]{dom}"
              f" · modelo: {p['model']}")
    cols = filename_collisions(plans)
    if cols:
        print("\n⚠ AVISO: proyectos derivados del nombre de archivo REPETIDOS "
              "(migrarían al MISMO proyecto, como familia):")
        for k, rels in sorted(cols.items()):
            print(f"    «{k}»: {', '.join(rels)}")
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

    if args.folder:
        root = Path(args.folder).expanduser()
        if not root.is_dir():
            print(f"⛔ No es una carpeta: {root}")
            return 2
        files = discover_xmls(root)
        if not files:
            print(f"⛔ No hay .xml bajo {root} (búsqueda recursiva).")
            return 2
        plans = plan_files(files, root)
        steps = oneshot_steps(plans, force=args.force, base_title=args.base_title)
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
        plans = plan_files([xml], xml.parent)
        if args.project:
            plans[0]["project"] = args.project.strip()
            plans[0]["source"] = "--project"
        steps = append_steps(plans[0], force=args.force)
        _print_plan("APPEND (no destructivo) — plan", plans, steps)
        if not args.apply:
            print("\nDRY-RUN — nada ejecutado. Repite con --apply.")
            return 0

    results = execute(steps, force=args.force)
    _print_summary(results)
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
