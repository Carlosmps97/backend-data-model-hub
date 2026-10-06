"""Orquestador de la migración Erwin (doc 54 + doc 77): ONE-SHOT por carpeta y APPEND.

Dos modos mutuamente excluyentes:

ONE-SHOT (destructivo) — deja la plataforma como un primer deployment:
  .venv/bin/python -m scripts.run_migration --folder "ruta/carpeta"          # dry-run
  .venv/bin/python -m scripts.run_migration --folder "ruta/carpeta" --apply [--force]
  Descubre TODOS los .xml (recursivo, orden determinista por subruta) y corre:
  quality POR PROYECTO (gate + glosario cruzado sólo entre los archivos que
  se unen en un mismo proyecto) → reset DESTRUCTIVO → create_admin (4 roles
  de caja + admin + whitelist SSO) → migrate archivo por archivo → audit →
  seed_ddl_export_rules ("data functions", en todos los proyectos; los de
  `ORACLE_PROJECTS` quedan sin reglas y con Oracle por default — doc 101) →
  seed_upload_profiles (perfil de carga «Plantilla BCP», doc 78) →
  seed_sheet_templates (plantilla Excel «QA_MODELO», doc 95) →
  mark_base_version (v1 de cada proyecto, título parametrizable).
  Doc 109: sin auto-arrange (el layout viene de Erwin); el gate deja el modelo
  parseado en caché para migrate; audit y seeds corren en paralelo.

APPEND (no destructivo) — suma UN archivo sobre la base viva:
  .venv/bin/python -m scripts.run_migration --append "ruta/modelo.xml" --apply
  quality → crosscheck (vs BD) → migrate → audit.
  Sin reset, sin admin, sin seeds, sin marcador (v1 ya existe).

Proyecto destino por archivo (doc 77, deroga el manifiesto de la D9 del doc 75):
lo decide la CONVENCIÓN de ubicación, no un archivo declarativo —

  <raíz>/MODELO DDV/*.xml   → proyecto «MODELO DDV»  (la subcarpeta fusiona)
  <raíz>/UDV INT FISICO.xml → proyecto «UDV INT FISICO»  (suelto = un proyecto)

es decir: un .xml dentro de una subcarpeta pertenece al proyecto que se llama
como esa PRIMERA subcarpeta (a cualquier profundidad), y un .xml suelto en la
raíz es su propio proyecto con el nombre del archivo. Dos orígenes distintos
que caen al mismo nombre ignorando mayúsculas son un error ANTES de tocar la
BD, nunca un merge silencioso. Del `<Locator>` del XML sólo se conservan
dominio y modelo (capas del Mart) como origen informativo. `--project` (solo
--append) fuerza un destino explícito.

El apply corre en ETAPAS (doc 77 §4). Dentro de un proyecto los archivos van
SECUENCIALES a propósito: la unicidad de nombres físicos POR PROYECTO (política
`_DUPn`) depende del prefetch que cada archivo hace contra la BD al empezar.
Entre proyectos DISTINTOS no hay nada compartido (doc 75: `projectId` en todo
doc, ids namespaceados), así que los gates y los migrate corren en CARRILES
paralelos — un carril por proyecto, hasta `--jobs` a la vez (default 4; con
`--jobs 1` todo vuelve a ser secuencial con la salida en vivo).

Manejo de fallas por paso:
  - quality (gate)        → detiene TODO antes de borrar nada (salvo --force);
                            los otros carriles del gate igual terminan (son
                            de solo lectura) y recién ahí se aborta
  - reset / create_admin  → detienen el resto (base a medio wipe: no seguir)
  - migrate / audit / seeds / mark_base → la falla se registra, se continúa
    con el resto y el exit code final es 1 (falla VISIBLE, nunca silenciosa)
  - crosscheck            → informativo: warning, no afecta el exit code

Los pasos invocan los MISMOS scripts del repo (cero lógica duplicada), cada
uno como subproceso propio (memoria liberada entre parses de cientos de MB)
con la salida COMPLETA — nada se resume ni se oculta. En un carril paralelo no
se puede streamear (varios procesos escribiendo a la vez es ilegible): la
salida se captura y se imprime entera, en bloque, apenas termina el paso.

Doc 110: mientras tanto, cada 30 s se imprime una línea por paso en curso con
su avance (docs, MB, velocidad, reintentos, conexiones lentas descartadas: lo
reporta el puente sync en `DMH_PROGRESS_FILE`), y la salida completa de cada
paso queda en `migration-reports/run-<ts>/NN-<paso>.log`; el resumen JSON
lleva el `log` de cada paso y, si falló, sus últimas líneas (`tail`). El plan
muestra la subcarpeta en Models de cada archivo (= nombre del archivo).
"""
from __future__ import annotations

import argparse
import codecs
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from xml.sax.saxutils import unescape

from scripts.erwin_migration.policies import parse_mart_locator as parse_locator
from scripts.erwin_migration.policies import source_folder_name

ROOT = Path(__file__).resolve().parent.parent
_PY = sys.executable

# Doc 110: avance y logs de cada paso. `_RUN["dir"]` es la carpeta de logs del
# run (la fija `execute`); `_RUNNING`, los pasos en curso que el latido lista.
PROGRESS_ENV = "DMH_PROGRESS_FILE"     # = app.core.db.sync.PROGRESS_ENV
HEARTBEAT_S = 30.0
_TAIL_LINES = 40
_RUN: dict = {"dir": None, "seq": 0}
_RUNNING: dict[int, dict] = {}
_RUNNING_LOCK = threading.Lock()
_LINE_OPEN = [False]     # la salida en vivo dejó una línea a medias (sin «\n»)
_STOP = threading.Event()  # Ctrl+C en una etapa paralela: los carriles no arrancan más pasos
_COLL_LABEL = {
    "canonical_columns": "columnas", "canonical_tables": "tablas", "views": "vistas",
    "relationships": "relaciones", "subject_areas": "canvases", "folders": "carpetas",
    "parent_domains": "dominios", "glossary_terms": "glosario", "udp_definitions": "UDP",
}

_LOCATOR_RE = re.compile(rb"<Locator>([^<]{1,600})</Locator>")
_PEEK_CHUNK = 8 * 1024 * 1024
_PEEK_CAP = 64 * 1024 * 1024

# Manifiesto retirado por el doc 77: si quedó uno en la carpeta, se avisa.
LEGACY_MANIFEST = "projects.json"

DEFAULT_JOBS = 4

_PRINT = threading.Lock()   # serializa los bloques de salida de los carriles


class DiscoveryError(ValueError):
    """Los .xml de la carpeta no dan un mapa de proyectos sin ambigüedad: se
    aborta ANTES de tocar la BD (dos orígenes al mismo proyecto sería un merge
    silencioso de modelos que nadie pidió)."""


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


def project_of(rel: str) -> str:
    """Proyecto destino de una subruta (doc 77 §3): el nombre de la PRIMERA
    subcarpeta, o el del archivo si está suelto en la raíz."""
    parts = rel.split("/")
    if len(parts) > 1:
        return parts[0].strip()
    return os.path.splitext(parts[0])[0].strip()


def _check_collisions(plans: list[dict]) -> None:
    """Dos ORÍGENES distintos (subcarpetas o archivos sueltos) que caen al mismo
    nombre de proyecto ignorando mayúsculas."""
    origins: dict[str, dict[str, str]] = {}
    for p in plans:
        key = p["rel"].split("/")[0] if p["source"] == "carpeta" else p["rel"]
        origins.setdefault(p["project"].lower(), {}).setdefault(key, p["project"])
    for keys in origins.values():
        if len(keys) > 1:
            name = next(iter(keys.values()))
            raise DiscoveryError(
                f"nombre de proyecto repetido «{name}»: " + ", ".join(sorted(keys))
                + " — una subcarpeta y un .xml suelto (o dos nombres que sólo "
                  "difieren en mayúsculas) caen al mismo proyecto; renombra uno")


def plan_files(files: list[Path], root: Path,
               locator_of=read_locator) -> list[dict]:
    """Proyecto destino por archivo según la CONVENCIÓN del doc 77. Dominio y
    modelo salen del Locator del Mart (informativos); `size` alimenta el peso
    del carril. `locator_of` es inyectable (tests)."""
    plans = []
    for f in files:
        rel = str(f.relative_to(root)).replace(os.sep, "/")
        loc = locator_of(f)
        parsed = parse_locator(loc) if loc else None
        plans.append({
            "path": f, "rel": rel,
            "project": project_of(rel),
            # Doc 110: subcarpeta del archivo en Models (la misma regla que migrate).
            "folder": source_folder_name(rel),
            "size": f.stat().st_size if f.is_file() else 0,
            "domain": parsed["domain"] if parsed else "",
            "model": parsed["model"] if parsed else f.stem.strip(),
            "mart": parsed is not None,      # el XML trae Locator del Mart
            "source": "carpeta" if "/" in rel else "archivo",
        })
    _check_collisions(plans)
    return plans


def group_by_project(plans: list[dict]) -> list[tuple[str, list[dict]]]:
    """[(proyecto, [planes])] en orden de primera aparición."""
    groups: dict[str, list[dict]] = {}
    for p in plans:
        groups.setdefault(p["project"], []).append(p)
    return list(groups.items())


# ── Pasos y etapas (construcción pura, testeable) ────────────────────────────
# klass: gate  = detiene todo salvo --force (corre ANTES de borrar nada)
#        abort = su falla detiene el resto del pipeline
#        core  = su falla marca el run como fallido, pero se continúa
#        check = validación: igual que core (falla visible, no detiene)
#        info  = informativo: warning, no afecta el exit code
#
# Una ETAPA es {"kind": "seq", "steps": [...]} (en orden, salida en vivo) o
# {"kind": "par", "lanes": [{"lane", "weight", "steps"}]} (carriles a la vez,
# los pasos DE UN carril siempre en orden).

# Doc 109: los pasos del one-shot que corren DESPUÉS de create_admin no
# repiten el DDL de arranque (create_admin acaba de crear tablas e índices);
# con la latencia de Lakebase eran ~7 s por proceso. Ver app/core/db/client.py.
_AFTER_ADMIN_ENV = {"DMH_SKIP_STARTUP_DDL": "1"}


def _quality_cmd(xmls: list[str], quality_json: str | None,
                 parse_cache: str | None = None) -> list[str]:
    """`--json` va ANTES de los archivos: los .xml siguen siendo los últimos args.
    Doc 109: con `parse_cache`, el gate deja cada modelo parseado para migrate."""
    extra = (["--parse-cache", parse_cache] if parse_cache else []) + (
        ["--json", quality_json] if quality_json else [])
    return [_PY, "-m", "scripts.erwin_migration.quality", *extra, *xmls]


def _migrate_cmd(p: dict, force: bool, parse_cache: str | None = None) -> list[str]:
    cmd = [_PY, "-m", "scripts.erwin_migration.migrate", str(p["path"])]
    if parse_cache:
        cmd += ["--parse-cache", parse_cache]      # doc 109: reusa el parse del gate
    cmd += ["--project", p["project"], "--apply"]
    if force:
        cmd.append("--force")
    return cmd


def _quality_json_of(quality_json: str | None, n: int) -> str | None:
    """Un reporte JSON del gate por proyecto: `<base>-<n>.json`."""
    return f"{quality_json[:-5]}-{n}.json" if quality_json else None


def _lane(project: str, steps: list[dict], weight: int) -> dict:
    return {"lane": project, "weight": weight, "steps": steps}


def oneshot_stages(plans: list[dict], *, force: bool, base_title: str,
                   quality_json: str | None = None, parse_cache: str | None = None) -> list[dict]:
    """Doc 109: sin auto-arrange (las posiciones vienen de Erwin, escaladas y
    separadas por migrate — `erwin_migration/layout.py`); el cierre corre sus
    pasos independientes en paralelo y recién después marca la versión base."""
    gates, migrates = [], []
    for n, (project, group) in enumerate(group_by_project(plans), start=1):
        weight = sum(p.get("size") or 0 for p in group)
        gates.append(_lane(project, [{
            "name": f"quality «{project}» (gate + glosario cruzado del proyecto)",
            "klass": "gate",
            "cmd": _quality_cmd([str(p["path"]) for p in group],
                                _quality_json_of(quality_json, n), parse_cache)}], weight))
        migrates.append(_lane(project, [{
            "name": f"migrate {p['rel']} → «{project}»",
            "klass": "core", "env": _AFTER_ADMIN_ENV,
            "cmd": _migrate_cmd(p, force, parse_cache)} for p in group], weight))
    closing = [
        ("audit", 4, {"name": "audit_data_consistency", "klass": "check",
                      "cmd": [_PY, "-m", "scripts.audit_data_consistency"]}),
        ("ddl", 3, {"name": "seed_ddl_export_rules (data functions, por proyecto)", "klass": "core",
                    "cmd": [_PY, "-m", "scripts.seed_ddl_export_rules", "--all-projects", "--apply"]}),
        ("uploads", 2, {"name": "seed_upload_profiles (perfil «Plantilla BCP», por proyecto)", "klass": "core",
                        "cmd": [_PY, "-m", "scripts.seed_upload_profiles", "--all-projects", "--apply"]}),
        ("sheets", 1, {"name": "seed_sheet_templates (plantilla Excel «QA_MODELO», por proyecto)",
                       "klass": "core",
                       "cmd": [_PY, "-m", "scripts.seed_sheet_templates", "--all-projects", "--apply"]}),
    ]
    return [
        {"kind": "par", "title": "gates de calidad (un carril por proyecto)",
         "lanes": gates},
        {"kind": "seq", "title": "reset destructivo + accesos", "steps": [
            {"name": "reset DESTRUCTIVO (borra todo el schema)", "klass": "abort",
             "cmd": [_PY, "-m", "scripts.reset_for_migration", "--apply"]},
            {"name": "create_admin (4 roles + admin + whitelist SSO)", "klass": "abort",
             "cmd": [_PY, "scripts/create_admin.py"]},
        ]},
        {"kind": "par", "title": "migrate (un carril por proyecto)",
         "lanes": migrates},
        # Doc 109: independientes entre sí (el audit sólo lee; cada seed escribe
        # su propia colección) — en paralelo.
        {"kind": "par", "title": "cierre (validación y seeds, en paralelo)",
         "lanes": [_lane(name, [{**step, "env": _AFTER_ADMIN_ENV}], weight)
                   for name, weight, step in closing]},
        {"kind": "seq", "title": "versión base (v1)", "steps": [
            {"name": "mark_base_version (v1 de cada proyecto)", "klass": "core",
             "env": _AFTER_ADMIN_ENV,
             "cmd": [_PY, "-m", "scripts.mark_base_version", "--apply",
                     "--title", base_title]},
        ]},
    ]


def append_stages(plan: dict, *, force: bool,
                  quality_json: str | None = None, parse_cache: str | None = None) -> list[dict]:
    """Doc 109: sin auto-arrange — re-armaría TODOS los canvases del proyecto
    (también los ya migrados) y borraría el layout de Erwin."""
    xml = str(plan["path"])
    return [{"kind": "seq", "title": f"append → «{plan['project']}»", "steps": [
        {"name": "quality (gate)", "klass": "gate",
         "cmd": _quality_cmd([xml], quality_json, parse_cache)},
        {"name": f"crosscheck vs BD viva del proyecto «{plan['project']}»", "klass": "info",
         "cmd": [_PY, "-m", "scripts.erwin_migration.crosscheck",
                 "--project", plan["project"], xml]},
        {"name": f"migrate {plan['rel']} → «{plan['project']}»", "klass": "core",
         "cmd": _migrate_cmd(plan, force, parse_cache)},
        {"name": "audit_data_consistency", "klass": "check",
         "cmd": [_PY, "-m", "scripts.audit_data_consistency"]},
    ]}]


def _iter_steps(stage: dict):
    """(carril|None, paso) de una etapa, en orden natural (el del plan)."""
    if stage["kind"] == "seq":
        for st in stage["steps"]:
            yield None, st
    else:
        for lane in stage["lanes"]:
            for st in lane["steps"]:
                yield lane["lane"], st


def _stage_steps(stage: dict) -> list[dict]:
    return [st for _, st in _iter_steps(stage)]


def _dispatch_order(lanes: list[dict]) -> list[int]:
    """Índices de los carriles de mayor a menor peso: con menos trabajos que
    carriles, el proyecto grande no puede quedar de cola."""
    return sorted(range(len(lanes)), key=lambda i: (-(lanes[i]["weight"] or 0), i))


# ── Ejecución ────────────────────────────────────────────────────────────────

def _run_step(step: dict, lane: str | None = None) -> tuple[int, float]:
    """Corre un paso. Sin carril, la salida se streamea en vivo; dentro de un
    carril paralelo se CAPTURA y se imprime COMPLETA al terminar (en bloque,
    bajo lock) — nada se resume ni se oculta.

    Doc 110: con carpeta de logs (`execute(log_dir=…)`), la salida completa
    queda también en `NN-<paso>.log` mientras el paso corre (`step["log"]`), las
    últimas líneas de un paso fallido en `step["tail"]`, y el paso recibe su
    archivo de progreso (`DMH_PROGRESS_FILE`) para el latido. El log es lo de
    menos: si no se puede escribir, el paso sigue igual."""
    head = f"▶ {step['name']}" if lane is None else f"▶ [{lane}] {step['name']}"
    cmd = shlex.join(step["cmd"])
    step.pop("log", None)
    step.pop("tail", None)
    # Doc 110: el paso escribe en UTF-8 aunque su salida vaya por tubería (en
    # Windows sería cp1252 y el «≠»/«→»/«⚠» de los resúmenes lo hacía caer).
    env = {**os.environ, **(step.get("env") or {}), "PYTHONIOENCODING": "utf-8"}
    log_path = progress = None
    if _RUN["dir"]:
        with _RUNNING_LOCK:
            _RUN["seq"] += 1
            seq = _RUN["seq"]
        base = os.path.join(_RUN["dir"], f"{seq:02d}-" + re.sub(
            r"[^A-Za-z0-9._-]+", "_", f"{lane + ' ' if lane else ''}{step['name']}")[:80].strip("_"))
        log_path, progress = f"{base}.log", f"{base}.progress.json"
        env = {**env, PROGRESS_ENV: progress, "PYTHONUNBUFFERED": "1"}
    key = id(step)
    with _RUNNING_LOCK:
        _RUNNING[key] = {"head": head.removeprefix("▶ "), "t0": time.monotonic(), "progress": progress}
    out = ""
    try:
        if lane is None:
            print(f"\n{'━' * 72}\n{head}\n  $ {cmd}\n", flush=True)
            t0 = time.perf_counter()
            if log_path is None:
                rc = subprocess.run(step["cmd"], cwd=ROOT, env=env).returncode
            else:
                rc, out, log_path = _pump(step["cmd"], env, log_path, echo=True)
            secs = time.perf_counter() - t0
        else:
            with _PRINT:
                print(f"\n{'┄' * 72}\n{head}\n  $ {cmd}\n  (en paralelo: su salida "
                      f"completa se imprime cuando termine)", flush=True)
            t0 = time.perf_counter()
            rc, out, log_path = _pump(step["cmd"], env, log_path, echo=False)
            secs = time.perf_counter() - t0
            with _PRINT:
                _forget(key)          # el latido ya no lo lista después de su «terminó»
                print(f"\n{'━' * 72}\n{head} — terminó en {secs:.1f}s (rc={rc})\n"
                      f"  $ {cmd}\n", flush=True)
                print(out, end="", flush=True)
                _LINE_OPEN[0] = bool(out) and not out.endswith("\n")
    finally:
        _forget(key)
        if progress is not None:      # sólo sirve mientras el paso corre
            for path in (progress, f"{progress}.tmp"):
                try:
                    os.remove(path)
                except OSError:
                    pass
    if log_path is not None:
        step["log"] = log_path
    if rc != 0 and out:
        step["tail"] = out.rstrip("\n").splitlines()[-_TAIL_LINES:]
    return rc, secs


def _forget(key: int) -> None:
    with _RUNNING_LOCK:
        _RUNNING.pop(key, None)


def _pump(cmd: list[str], env: dict, log_path: str | None, *,
          echo: bool) -> tuple[int, str, str | None]:
    """Corre el paso leyendo su salida por trozos: cada trozo va al log en el
    acto (se puede seguir con `tail -f`) y, con `echo`, a la pantalla (un
    `input()` sin salto de línea también se ve). Si el log falla, se avisa y
    se sigue sin él. Si el orquestador se interrumpe o falla, detiene al paso,
    como `subprocess.run`. Devuelve (rc, salida, log o None)."""
    log = None
    if log_path is not None:
        try:
            log = open(log_path, "wb", buffering=0)
        except OSError as exc:
            _log_unavailable(log_path, exc)
            log_path = None
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    chunks: list[str] = []
    try:
        p = subprocess.Popen(cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except BaseException:
        if log is not None:
            log.close()
        raise
    assert p.stdout is not None
    try:
        fd = p.stdout.fileno()
        while True:
            data = os.read(fd, 65536)
            if not data:
                break
            if log is not None:
                try:
                    log.write(data)
                except OSError as exc:
                    _log_unavailable(log_path, exc)
                    log.close()
                    log = None
            text = decoder.decode(data)
            chunks.append(text)
            if echo:
                _echo(text)
        rc = p.wait()
    except BaseException:
        try:
            p.wait(timeout=0.25)
        except BaseException:       # no terminó, o un segundo Ctrl+C: igual se detiene
            pass
        if p.poll() is None:
            p.kill()
            p.wait()
        # Lo que el paso alcanzó a imprimir al interrumpirse (rollback,
        # traceback) también queda en el log y en pantalla.
        for data in _leftover(p.stdout.fileno()):
            try:
                if log is not None:
                    log.write(data)
                if echo:
                    _echo(decoder.decode(data))
            except Exception:  # noqa: BLE001 — la pantalla o el log ya no responden
                break
        raise
    finally:
        p.stdout.close()
        if log is not None:
            log.close()
    return rc, "".join(chunks) + decoder.decode(b"", final=True), log_path


def _leftover(fd: int):
    """Lo que quedó en la tubería de un paso ya detenido, sin bloquear (un
    nieto que la mantenga abierta no cuelga al orquestador)."""
    try:
        os.set_blocking(fd, False)
    except (AttributeError, OSError):
        return
    while True:
        try:
            data = os.read(fd, 65536)
        except OSError:            # BlockingIOError: no hay más por ahora
            return
        if not data:
            return
        yield data


def _echo(text: str) -> None:
    """Salida en vivo de un paso, con el turno de impresión (el latido no se
    mete a mitad de una línea suya)."""
    if not text:
        return
    with _PRINT:
        sys.stdout.write(text)
        sys.stdout.flush()
        _LINE_OPEN[0] = not text.endswith("\n")


def _log_unavailable(log_path: str | None, exc: OSError) -> None:
    with _PRINT:
        print(f"\n⚠ no se pudo escribir el log {log_path} ({exc}); el paso sigue sin él",
              flush=True)


def _progress_line(head: str, secs: float, progress: dict | None) -> str:
    """Una línea del latido: tiempo del paso y lo que el paso reporta."""
    m, s = divmod(int(secs), 60)
    parts = [f"⏱ {m}:{s:02d}", head]
    if progress:
        coll = progress.get("coleccion")
        if coll:
            parts.append(_COLL_LABEL.get(coll, coll))
        parts.append(f"{progress.get('docs', 0):,} docs")
        parts.append(f"{progress.get('bytes', 0) / 1e6:,.0f} MB")
        kbps = progress.get("kbps") or 0
        rate = f"{kbps / 1000:.1f} MB/s" if kbps >= 1000 else f"{kbps:.0f} KB/s"
        parts.append(rate + (" ⚠ red lenta" if 0 < kbps < 200 else ""))
        if progress.get("reintentos"):
            parts.append(f"{progress['reintentos']} reintentos")
        if progress.get("descartadas"):
            parts.append(f"{progress['descartadas']} conexiones lentas descartadas")
    return " · ".join(parts)


def _read_progress(path: str | None) -> dict | None:
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _heartbeat(stop: threading.Event, every: float) -> None:
    """Cada `every` s, una línea por paso en curso: un paso que sube mucho (o
    por una red lenta) ya no parece colgado."""
    while not stop.wait(every):
        # La foto se toma CON el turno de impresión: un carril que está
        # imprimiendo su «terminó» ya no aparece.
        with _PRINT:
            now = time.monotonic()
            with _RUNNING_LOCK:
                running = list(_RUNNING.values())
            lines = [_progress_line(r["head"], now - r["t0"], _read_progress(r["progress"]))
                     for r in running]
            if lines:
                print(("\n" if _LINE_OPEN[0] else "") + "\n".join(lines), flush=True)
                _LINE_OPEN[0] = False


def _classify(step: dict, lane: str | None, rc: int, secs: float,
              force: bool) -> tuple[dict, bool]:
    """(resultado, ¿detiene lo que sigue?) según la clase del paso."""
    estado, fatal, stops = "OK", False, False
    if rc != 0:
        if step["klass"] == "gate":
            if force:
                estado = "CON ERRORS (--force: continúa)"
            else:
                estado, fatal, stops = "ERROR — DETIENE (gate)", True, True
        elif step["klass"] == "abort":
            estado, fatal, stops = "ERROR — DETIENE", True, True
        elif step["klass"] in ("core", "check"):
            estado, fatal = "ERROR", True
        else:
            estado = "WARNING (informativo)"
    return ({**step, "lane": lane, "rc": rc, "secs": secs,
             "estado": estado, "fatal": fatal}, stops)


def _omitted(step: dict, lane: str | None) -> dict:
    return {**step, "lane": lane, "rc": None, "secs": 0.0,
            "estado": "OMITIDO", "fatal": False}


def _run_lanes(lanes: list[dict], *, force: bool, jobs: int) -> tuple[list[dict], bool]:
    """Corre los carriles a la vez (pasos de un carril en orden). Devuelve los
    resultados en orden de CARRIL —no de llegada— y si algo detiene lo que sigue."""
    def corre(lane: dict) -> list[tuple[dict, bool]]:
        out = []
        for st in lane["steps"]:
            if _STOP.is_set():          # Ctrl+C: el resto del carril no arranca
                out.append((_omitted(st, lane["lane"]), False))
                continue
            out.append(_classify(st, lane["lane"], *_run_step(st, lane["lane"]), force))
        return out

    done: dict[int, list[tuple[dict, bool]]] = {}
    with ThreadPoolExecutor(max_workers=min(jobs, len(lanes))) as ex:
        futures = {ex.submit(corre, lanes[i]): i for i in _dispatch_order(lanes)}
        try:
            for fut in as_completed(futures):
                done[futures[fut]] = fut.result()
        except BaseException:
            # El Ctrl+C llega sólo a este hilo: sin esto, cada carril seguía
            # arrancando sus pasos (migrates que escriben) hasta el final.
            _STOP.set()
            raise
    results = [r for i in range(len(lanes)) for r, _ in done[i]]
    stops = any(s for i in range(len(lanes)) for _, s in done[i])
    return results, stops


def execute(stages: list[dict], *, force: bool, jobs: int = 1, log_dir: str | None = None,
            heartbeat_s: float = HEARTBEAT_S) -> list[dict]:
    """Corre las etapas en orden con la política de fallas del docstring. Una
    etapa paralela se corre entera (sus carriles son independientes) y recién
    después se evalúa si algo aborta lo que sigue. Doc 110: con `log_dir`, la
    salida de cada paso queda en un log; mientras tanto, un latido cada
    `heartbeat_s` lista los pasos en curso con su avance."""
    previous = dict(_RUN)
    _RUN.update({"dir": log_dir, "seq": 0})
    _STOP.clear()
    stop = threading.Event()
    beat = threading.Thread(target=_heartbeat, args=(stop, heartbeat_s), daemon=True)
    beat.start()
    try:
        return _execute(stages, force=force, jobs=jobs)
    finally:
        stop.set()
        beat.join(timeout=5)
        _RUN.update(previous)


def _execute(stages: list[dict], *, force: bool, jobs: int) -> list[dict]:
    results: list[dict] = []
    abort = False
    for stage in stages:
        if abort:
            results += [_omitted(st, lane) for lane, st in _iter_steps(stage)]
            continue
        lanes = stage["lanes"] if stage["kind"] == "par" else None
        if lanes and jobs > 1 and len(lanes) > 1:
            res, stops = _run_lanes(lanes, force=force, jobs=jobs)
            results += res
            abort = abort or stops
            continue
        for lane, st in _iter_steps(stage):
            if abort:
                results.append(_omitted(st, lane))
                continue
            r, stops = _classify(st, lane, *_run_step(st), force)
            abort = abort or stops
            results.append(r)
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


def _print_summary(results: list[dict], wall: float, jobs: int) -> None:
    print(f"\n{'═' * 72}\nRESUMEN DEL RUN")
    for r in results:
        secs = f"{r['secs']:7.1f}s" if r["rc"] is not None else "      —"
        lane = f"[{r['lane']}] " if r.get("lane") else ""
        print(f"  [{r['estado']:<32}] {secs}  {lane}{r['name']}")
        if r.get("rc") not in (0, None) and r.get("log"):
            # Doc 110: dónde está la salida completa y cómo terminó.
            print(f"        log: {r['log']}")
            for line in (r.get("tail") or [])[-6:]:
                print(f"        │ {line}")
    total = sum(r["secs"] for r in results)
    print(f"\n  tiempo real {wall / 60:.1f} min · suma de los pasos {total / 60:.1f} min "
          f"(hasta {jobs} proyecto(s) en paralelo)")


def _write_summary(mode: str, plans: list[dict], results: list[dict],
                   started: str, jobs: int) -> str:
    out = os.path.join(str(ROOT), "migration-reports",
                       f"run_migration-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump({
            "mode": mode, "startedAt": started, "jobs": jobs,
            "finishedAt": datetime.now(timezone.utc).isoformat(),
            "files": [{"rel": p["rel"], "project": p["project"], "folder": p.get("folder"),
                       "domain": p["domain"], "model": p["model"],
                       "source": p["source"], "bytes": p.get("size")} for p in plans],
            "steps": [{"name": r["name"], "lane": r.get("lane"), "rc": r["rc"],
                       "estado": r["estado"], "secs": round(r["secs"], 1),
                       # Doc 110: salida completa del paso y, si falló, su final.
                       **({"log": r["log"]} if r.get("log") else {}),
                       **({"tail": r["tail"]} if r.get("tail") and r["rc"] not in (0, None) else {})}
                      for r in results],
        }, fh, ensure_ascii=False, indent=1)
    return out


def _print_stage(i: int, stage: dict) -> None:
    if stage["kind"] == "seq":
        print(f"  {i}. [seq] {stage['title']}")
        for st in stage["steps"]:
            print(f"          [{st['klass']:<5}] {st['name']}")
            print(f"            $ {shlex.join(st['cmd'])}")
        return
    print(f"  {i}. [par] {stage['title']} — {len(stage['lanes'])} carril(es)")
    for lane in stage["lanes"]:
        print(f"      carril «{lane['lane']}» ({(lane['weight'] or 0) / 1e6:,.0f} MB)")
        for st in lane["steps"]:
            print(f"          [{st['klass']:<5}] {st['name']}")
            print(f"            $ {shlex.join(st['cmd'])}")


def _print_plan(title: str, plans: list[dict], stages: list[dict], jobs: int) -> None:
    print(f"{'═' * 72}\n{title}\n{'═' * 72}")
    print(f"\nARCHIVOS ({len(plans)}) — proyecto destino por CONVENCIÓN (doc 77): "
          f"subcarpeta = 1 proyecto (fusiona sus .xml); .xml suelto = 1 proyecto:")
    for p in plans:
        mb = (p.get("size") or 0) / 1e6
        origin = ("" if not p.get("mart") else
                  f" · Mart: {p['domain']} / {p['model']}" if p["domain"]
                  else f" · Mart: {p['model']}")
        print(f"  {p['rel']}  ({mb:,.0f} MB)")
        # Doc 110: la subcarpeta en Models es el nombre del archivo.
        print(f"      → proyecto «{p['project']}» · subcarpeta «{p['folder']}» "
              f"[{p['source']}]{origin}")
    groups = group_by_project(plans)
    print(f"\nPROYECTOS ({len(groups)}):")
    for project, group in groups:
        print(f"  «{project}»")
        for g in group:
            print(f"      · {g['rel']}")
    print(f"\nETAPAS ({len(stages)}) — hasta {jobs} proyecto(s) en paralelo "
          f"(los archivos de un mismo proyecto siempre en orden):")
    for i, stage in enumerate(stages, 1):
        _print_stage(i, stage)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Orquestador de migración Erwin: one-shot por carpeta "
                    "(destructivo) o append de un archivo (doc 54 + doc 77)")
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
    ap.add_argument("--jobs", type=int, default=DEFAULT_JOBS,
                    help=f"proyectos en paralelo (default {DEFAULT_JOBS}; 1 = todo "
                         "secuencial con la salida en vivo)")
    ap.add_argument("--base-title", default="Base - Migración Erwin (XML)",
                    help="(one-shot) título del marcador de versión base v1")
    args = ap.parse_args(argv)

    if bool(args.folder) == bool(args.append):
        ap.error("exactamente uno: --folder (one-shot) o --append (un archivo)")
    if args.project and not args.append:
        ap.error("--project es solo para --append")
    if args.jobs < 1:
        ap.error("--jobs debe ser >= 1")

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")   # los subprocesos heredan os.environ

    started = datetime.now(timezone.utc).isoformat()
    # Doc 109: el gate parsea cada XML una vez y deja el modelo acá; migrate lo
    # reusa. Carpeta temporal del run, se borra al terminar.
    parse_cache = tempfile.mkdtemp(prefix="erwin-parse-") if args.apply else None
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
        if (root / LEGACY_MANIFEST).is_file():
            print(f"⚠ {root / LEGACY_MANIFEST} YA NO SE USA (doc 77): el proyecto "
                  "sale de dónde está cada .xml. Puedes borrarlo.\n")
        try:
            plans = plan_files(files, root)
        except DiscoveryError as exc:
            print(f"⛔ Descubrimiento: {exc}")
            return 2
        stages = oneshot_stages(plans, force=args.force, base_title=args.base_title,
                                quality_json=quality_json, parse_cache=parse_cache)
        _print_plan("ONE-SHOT (DESTRUCTIVO) — plan", plans, stages, args.jobs)
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
        stages = append_stages(plans[0], force=args.force, quality_json=quality_json,
                               parse_cache=parse_cache)
        _print_plan("APPEND (no destructivo) — plan", plans, stages, args.jobs)
        if not args.apply:
            print("\nDRY-RUN — nada ejecutado. Repite con --apply.")
            return 0

    # Doc 110: la salida completa de cada paso queda en su log (un error ya no
    # se pierde con el scroll del terminal).
    run_dir = os.path.join(str(ROOT), "migration-reports",
                           f"run-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}")
    os.makedirs(run_dir, exist_ok=True)
    print(f"\nLogs de cada paso: {run_dir}")
    t0 = time.perf_counter()
    try:
        results = execute(stages, force=args.force, jobs=args.jobs, log_dir=run_dir)
    finally:
        if parse_cache:
            shutil.rmtree(parse_cache, ignore_errors=True)
    _print_summary(results, time.perf_counter() - t0, args.jobs)
    gate_failed = any(r["klass"] == "gate" and r.get("rc") not in (0, None) for r in results)
    if gate_failed and not args.force:
        _print_gate_help(quality_json)
    out = _write_summary("one-shot" if args.folder else "append",
                         plans, results, started, args.jobs)
    print(f"\nResumen del run: {out}")
    if any(r["fatal"] for r in results):
        print("RESULTADO: CON ERRORES (revisar arriba).")
        return 1
    print("RESULTADO: OK.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
