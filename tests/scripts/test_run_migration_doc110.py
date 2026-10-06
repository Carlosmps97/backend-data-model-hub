"""Doc 110 — el orquestador del one-shot muestra el avance y guarda la salida.

- Cada 30 s imprime una línea por paso en curso (tiempo + lo que el paso
  reporta: documentos, MB, velocidad, reintentos, conexiones descartadas). Un
  paso en paralelo ya no parece colgado mientras sube.
- La salida COMPLETA de cada paso queda en `migration-reports/run-<ts>/`; el
  resumen JSON lleva su `log` y, si falló, las últimas líneas (`tail`). El
  2026-10-05 el error de «Otros» sólo quedó en el terminal.
- El plan muestra la subcarpeta de cada XML (= nombre del archivo)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import scripts.run_migration as rm


def _py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_el_plan_muestra_la_subcarpeta_con_el_nombre_del_archivo(tmp_path):
    (tmp_path / "MODELO DDV").mkdir()
    files = [tmp_path / "MODELO DDV" / "DDV - CPYBCA.xml", tmp_path / "UDV EXT.xml"]
    for f in files:
        f.write_text("<x/>", encoding="utf-8")
    plans = rm.plan_files(files, tmp_path, locator_of=lambda f: "erwin://Mart://Mart/BCP/CPYBCA/M?V=1")
    assert [(p["project"], p["folder"]) for p in plans] == [
        ("MODELO DDV", "DDV - CPYBCA"), ("UDV EXT", "UDV EXT")]


def test_un_paso_en_paralelo_deja_su_salida_en_un_log_y_la_cola_si_falla(tmp_path, monkeypatch):
    monkeypatch.setitem(rm._RUN, "dir", str(tmp_path))
    step = {"name": "migrate MODELO DDV/Otros.xml → «MODELO DDV»", "klass": "core",
            "cmd": _py("print('procesando'); print('Traceback: boom'); import sys; sys.exit(3)")}
    rc, _secs = rm._run_step(step, "MODELO DDV")
    assert rc == 3
    log = Path(step["log"])
    assert log.parent == tmp_path and "Traceback: boom" in log.read_text(encoding="utf-8")
    assert step["tail"][-2:] == ["procesando", "Traceback: boom"]


def test_un_paso_secuencial_se_ve_en_vivo_y_tambien_queda_en_su_log(tmp_path, monkeypatch, capfd):
    monkeypatch.setitem(rm._RUN, "dir", str(tmp_path))
    step = {"name": "reset", "klass": "abort", "cmd": _py("print('borrando todo')")}
    rc, _secs = rm._run_step(step)
    assert rc == 0 and "tail" not in step
    assert "borrando todo" in capfd.readouterr().out
    assert "borrando todo" in Path(step["log"]).read_text(encoding="utf-8")


def test_el_paso_recibe_su_archivo_de_progreso(tmp_path, monkeypatch):
    monkeypatch.setitem(rm._RUN, "dir", str(tmp_path))
    step = {"name": "migrate a.xml", "klass": "core",
            "cmd": _py("import os; print(os.environ['DMH_PROGRESS_FILE'])")}
    rm._run_step(step, "P")
    printed = Path(step["log"]).read_text(encoding="utf-8").strip().splitlines()[-1]
    assert Path(printed).parent == tmp_path


def test_un_paso_que_imprime_simbolos_no_se_cae_aunque_la_consola_no_sea_utf8(tmp_path, monkeypatch):
    """En Windows la salida por tubería usa cp1252: el «≠» del resumen del
    migrate (o «→», «⚠») hacía caer al paso con UnicodeEncodeError."""
    monkeypatch.setitem(rm._RUN, "dir", str(tmp_path))
    step = {"name": "migrate a.xml", "klass": "core", "env": {"LC_ALL": "en_US.ISO8859-1"},
            "cmd": _py("print('columnas con tipo lógico ≠ físico → ⚠')")}
    rc, _secs = rm._run_step(step, "P")
    assert rc == 0
    assert "≠ físico → ⚠" in Path(step["log"]).read_text(encoding="utf-8")


def test_linea_de_avance():
    progress = {"docs": 81000, "bytes": 98_000_000, "kbps": 1600, "reintentos": 2,
                "descartadas": 3, "coleccion": "canonical_columns"}
    assert rm._progress_line("[MODELO DDV] migrate Otros.xml", 754, progress) == (
        "⏱ 12:34 · [MODELO DDV] migrate Otros.xml · columnas · 81,000 docs · 98 MB · "
        "1.6 MB/s · 2 reintentos · 3 conexiones lentas descartadas")
    assert rm._progress_line("reset", 65, None) == "⏱ 1:05 · reset"
    slow = {**progress, "kbps": 60, "reintentos": 0, "descartadas": 0}
    assert rm._progress_line("x", 30, slow).endswith("· 60 KB/s ⚠ red lenta")


def test_el_avance_se_imprime_mientras_un_paso_tarda(tmp_path, capfd):
    stages = [{"kind": "seq", "title": "t", "steps": [
        {"name": "paso largo", "klass": "core", "cmd": _py("import time; time.sleep(0.6)")}]}]
    results = rm.execute(stages, force=False, jobs=1, log_dir=str(tmp_path), heartbeat_s=0.1)
    assert results[0]["rc"] == 0
    assert "⏱ 0:00 · paso largo" in capfd.readouterr().out


def test_el_resumen_lleva_el_log_y_la_cola_del_paso_fallido(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(rm, "ROOT", tmp_path)
    results = [
        {"name": "migrate Otros", "lane": "DDV", "rc": 1, "estado": "ERROR", "secs": 2431.4,
         "log": "/r/03-otros.log", "tail": ["Traceback", "ConnectionDoesNotExistError: closed"]},
        {"name": "migrate UDV", "lane": "UDV", "rc": 0, "estado": "OK", "secs": 20.0, "log": "/r/04-udv.log"},
    ]
    out = rm._write_summary("one-shot", [], results, "2026-10-05T00:00:00", 4)
    steps = json.loads(Path(out).read_text(encoding="utf-8"))["steps"]
    assert steps[0]["log"] == "/r/03-otros.log" and steps[0]["tail"][-1].startswith("ConnectionDoesNotExist")
    assert steps[1]["log"] == "/r/04-udv.log" and "tail" not in steps[1]
    rm._print_summary(results, 2500.0, 4)
    printed = capsys.readouterr().out
    assert "log: /r/03-otros.log" in printed and "ConnectionDoesNotExistError: closed" in printed


# ── Revisión independiente (doc 110, ronda 1) ───────────────────────────────

import os
import threading
import time

import pytest


def test_si_el_log_no_se_puede_escribir_el_paso_en_paralelo_sigue_y_su_salida_se_ve(tmp_path, monkeypatch, capfd):
    """M1: un log que falla (disco lleno, permisos, carpeta borrada) no puede
    tumbar el one-shot ni perder la salida del paso, que ya escribió en la BD."""
    monkeypatch.setitem(rm._RUN, "dir", str(tmp_path / "no-existe"))
    step = {"name": "migrate a.xml", "klass": "core", "cmd": _py("print('escribió en la BD')")}
    rc, _secs = rm._run_step(step, "P")
    assert rc == 0 and "log" not in step
    assert "escribió en la BD" in capfd.readouterr().out


def test_si_el_log_no_se_puede_escribir_el_paso_secuencial_sigue_y_su_salida_se_ve(tmp_path, monkeypatch, capfd):
    monkeypatch.setitem(rm._RUN, "dir", str(tmp_path / "no-existe"))
    step = {"name": "reset", "klass": "abort", "cmd": _py("print('borrado')")}
    rc, _secs = rm._run_step(step)
    assert rc == 0 and "log" not in step
    assert "borrado" in capfd.readouterr().out


def test_un_error_al_borrar_el_progreso_no_tumba_el_paso(tmp_path, monkeypatch):
    monkeypatch.setitem(rm._RUN, "dir", str(tmp_path))
    real_remove = os.remove

    def remove(path):
        if path.endswith(".progress.json"):
            raise PermissionError(13, "lo está leyendo otro proceso")
        real_remove(path)

    monkeypatch.setattr(rm.os, "remove", remove)
    step = {"name": "migrate a.xml", "klass": "core",
            "cmd": _py("import os; open(os.environ['DMH_PROGRESS_FILE'], 'w').write('{}')")}
    assert rm._run_step(step, "P")[0] == 0


@pytest.mark.parametrize("lane", ["P", None])
def test_el_log_se_escribe_mientras_el_paso_corre(tmp_path, monkeypatch, lane):
    """L1: durante un migrate de 40 min el log ya tiene lo impreso (tail -f), y
    si el orquestador muere la salida no se pierde."""
    monkeypatch.setitem(rm._RUN, "dir", str(tmp_path))
    step = {"name": "migrate a.xml", "klass": "core",
            "cmd": _py("import time; print('inicio', flush=True); time.sleep(1.5)")}
    th = threading.Thread(target=rm._run_step, args=(step, lane))
    th.start()
    seen = False
    deadline = time.monotonic() + 1.2
    while time.monotonic() < deadline and not seen:
        seen = any("inicio" in p.read_text(encoding="utf-8", errors="replace")
                   for p in tmp_path.glob("*.log"))
        time.sleep(0.05)
    th.join()
    assert seen


def test_si_el_orquestador_se_interrumpe_el_paso_en_vivo_no_queda_corriendo(tmp_path, monkeypatch):
    """L2: como `subprocess.run`, un Ctrl+C (o cualquier error) del orquestador
    detiene al hijo en vez de dejarlo escribiendo en la BD."""
    monkeypatch.setitem(rm._RUN, "dir", str(tmp_path))
    pidfile = tmp_path / "pid"
    step = {"name": "migrate a.xml", "klass": "core",
            "cmd": _py(f"import os, time; open({str(pidfile)!r}, 'w').write(str(os.getpid())); "
                       "print('SALIDA' + '_DEL_HIJO', flush=True); time.sleep(30)")}

    class Interrupting:            # Ctrl+C justo cuando llega la salida del hijo
        def write(self, text):
            if "SALIDA_DEL_HIJO" in text:
                raise KeyboardInterrupt

        def flush(self):
            pass

    monkeypatch.setattr(rm.sys, "stdout", Interrupting())
    with pytest.raises(KeyboardInterrupt):
        rm._run_step(step)
    monkeypatch.undo()
    pid = int(pidfile.read_text())
    time.sleep(0.3)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_el_latido_no_lista_un_paso_que_ya_termino(capsys):
    """L3: el latido toma la foto de los pasos en curso CON el turno de
    impresión: si un carril está imprimiendo su «terminó», espera y ya no lo ve."""
    key = object()
    with rm._RUNNING_LOCK:
        rm._RUNNING[id(key)] = {"head": "[MODELO DDV] migrate Otros.xml", "t0": time.monotonic(), "progress": None}
    stop = threading.Event()
    with rm._PRINT:
        beat = threading.Thread(target=rm._heartbeat, args=(stop, 0.05), daemon=True)
        beat.start()
        time.sleep(0.3)
        with rm._RUNNING_LOCK:
            rm._RUNNING.pop(id(key))
        print("[MODELO DDV] migrate Otros.xml — terminó en 2431.4s (rc=0)")
    time.sleep(0.3)
    stop.set()
    beat.join(2)
    out = capsys.readouterr().out
    assert "⏱ 0:00 · [MODELO DDV] migrate Otros.xml" not in out[out.index("terminó en"):]


def test_la_cola_y_el_log_de_una_corrida_no_quedan_en_la_siguiente(tmp_path, monkeypatch):
    """L4: el mismo paso corrido dos veces (append, tests): la segunda, OK, no
    arrastra el `tail` de la primera."""
    monkeypatch.setitem(rm._RUN, "dir", str(tmp_path))
    step = {"name": "migrate a.xml", "klass": "core", "cmd": _py("import sys; print('boom'); sys.exit(3)")}
    rm._run_step(step, "P")
    assert step["tail"] == ["boom"]
    step["cmd"] = _py("print('ok')")
    rm._run_step(step, "P")
    assert "tail" not in step


def test_el_resumen_no_escribe_cola_de_un_paso_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(rm, "ROOT", tmp_path)
    results = [{"name": "a", "lane": "P", "rc": 0, "estado": "OK", "secs": 1.0, "log": "/r/a.log", "tail": ["viejo"]}]
    steps = json.loads(Path(rm._write_summary("one-shot", [], results, "t", 4)).read_text(encoding="utf-8"))["steps"]
    assert "tail" not in steps[0]


def test_el_latido_no_se_pega_a_una_linea_a_medias_del_paso(tmp_path, capfd):
    """L5: un paso que imprime un avance sin salto de línea («borrando
    120/400…») y el latido empieza en su propia línea."""
    stages = [{"kind": "seq", "title": "t", "steps": [
        {"name": "reset", "klass": "abort",
         "cmd": _py("import sys,time; sys.stdout.write('borrando 120/400 tablas...'); "
                    "sys.stdout.flush(); time.sleep(0.6); print(' listo')")}]}]
    rm.execute(stages, force=False, jobs=1, log_dir=str(tmp_path), heartbeat_s=0.1)
    out = capfd.readouterr().out
    assert "⏱" in out and "tablas...⏱" not in out


def test_el_plan_solo_dice_mart_cuando_el_xml_trae_locator(tmp_path, capsys):
    files = [tmp_path / "Local.xml", tmp_path / "DelMart.xml"]
    for f in files:
        f.write_text("<x/>", encoding="utf-8")
    plans = rm.plan_files(files, tmp_path, locator_of=lambda f: (
        "erwin://Mart://Mart/BCP/Otros/M V1?x" if f.name == "DelMart.xml" else None))
    rm._print_plan("t", plans, [], 1)
    out = capsys.readouterr().out
    assert "Mart: Otros / M V1" in out and out.count("Mart:") == 1


def test_el_resumen_lleva_la_subcarpeta_de_cada_archivo(tmp_path, monkeypatch):
    monkeypatch.setattr(rm, "ROOT", tmp_path)
    (tmp_path / "P").mkdir()
    f = tmp_path / "P" / "Otros.xml"
    f.write_text("<x/>", encoding="utf-8")
    plans = rm.plan_files([f], tmp_path, locator_of=lambda f: None)
    files = json.loads(Path(rm._write_summary("one-shot", plans, [], "t", 4)).read_text(encoding="utf-8"))["files"]
    assert files[0]["folder"] == "Otros"


def test_no_quedan_archivos_de_progreso_ni_temporales(tmp_path, monkeypatch):
    monkeypatch.setitem(rm._RUN, "dir", str(tmp_path))
    step = {"name": "migrate a.xml", "klass": "core",
            "cmd": _py("import os; p = os.environ['DMH_PROGRESS_FILE']; "
                       "open(p, 'w').write('{}'); open(p + '.tmp', 'w').write('{')")}
    rm._run_step(step, "P")
    assert sorted(p.name for p in tmp_path.iterdir() if not p.name.endswith(".log")) == []


@pytest.mark.skipif(os.name != "posix", reason="señal al grupo de procesos (Ctrl+C) sólo en POSIX")
def test_con_ctrl_c_lo_ultimo_que_imprime_el_paso_queda_en_su_log(tmp_path):
    """Revisión (ronda 1): Ctrl+C llega al orquestador Y al paso; lo que el paso
    imprime al interrumpirse («rollback…», traceback) no se pierde."""
    import signal
    import subprocess as sp

    pid_file = tmp_path / "hijo.pid"
    child = tmp_path / "hijo.py"
    child.write_text(
        "import os, time\n"
        f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
        "print('empezó', flush=True)\n"
        "try:\n    time.sleep(10)\n"
        "except KeyboardInterrupt:\n"
        "    print('ULTIMAS PALABRAS: rollback de la tabla X', flush=True)\n    raise\n",
        encoding="utf-8")
    wrapper = tmp_path / "orquestador.py"
    wrapper.write_text(
        "import sys\nimport scripts.run_migration as rm\n"
        f"rm._RUN['dir'] = {str(tmp_path)!r}\n"
        f"rm._run_step({{'name': 'paso', 'klass': 'core', 'cmd': [sys.executable, {str(child)!r}]}})\n",
        encoding="utf-8")
    orch = sp.Popen([sys.executable, str(wrapper)], cwd=str(rm.ROOT), stdout=sp.PIPE, stderr=sp.STDOUT,
                    env={**os.environ, "PYTHONPATH": str(rm.ROOT)}, start_new_session=True)
    deadline = time.monotonic() + 10
    while not pid_file.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(0.3)
    os.killpg(orch.pid, signal.SIGINT)
    orch.communicate(timeout=10)
    logs = "".join(p.read_text(encoding="utf-8", errors="replace") for p in tmp_path.glob("*.log"))
    assert "ULTIMAS PALABRAS" in logs


# ── Revisión independiente (doc 110, ronda 2) ───────────────────────────────

@pytest.mark.skipif(os.name != "posix", reason="señal al grupo de procesos (Ctrl+C) sólo en POSIX")
def test_ctrl_c_en_una_etapa_en_paralelo_no_arranca_mas_pasos(tmp_path):
    """El Ctrl+C llega al hilo principal; los carriles seguían arrancando sus
    pasos siguientes (migrates que escriben en la BD) hasta terminar todos."""
    import signal
    import subprocess as sp

    marks = tmp_path / "arrancados"
    marks.mkdir()

    def step(name):
        return {"name": name, "klass": "core",
                "cmd": [sys.executable, "-c",
                        f"import pathlib, time; pathlib.Path({str(marks)!r}, {name!r}).touch(); time.sleep(1)"]}

    stages = [{"kind": "par", "title": "migrate", "lanes": [
        {"lane": lane, "weight": 1, "steps": [step(f"{lane}-{i}") for i in (1, 2, 3)]} for lane in ("A", "B")]}]
    wrapper = tmp_path / "orquestador.py"
    wrapper.write_text(
        "import json, sys\nimport scripts.run_migration as rm\n"
        f"stages = json.loads({json.dumps(json.dumps(stages))})\n"
        f"rm.execute(stages, force=False, jobs=2, log_dir={str(tmp_path)!r})\n", encoding="utf-8")
    orch = sp.Popen([sys.executable, str(wrapper)], cwd=str(rm.ROOT), stdout=sp.PIPE, stderr=sp.STDOUT,
                    env={**os.environ, "PYTHONPATH": str(rm.ROOT)}, start_new_session=True)
    deadline = time.monotonic() + 10
    while len(list(marks.iterdir())) < 2 and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(0.2)
    os.killpg(orch.pid, signal.SIGINT)
    orch.communicate(timeout=30)
    assert sorted(p.name for p in marks.iterdir()) == ["A-1", "B-1"]


def test_un_segundo_ctrl_c_durante_la_espera_no_deja_el_paso_corriendo(tmp_path, monkeypatch):
    import subprocess as sp

    pid_file = tmp_path / "pid"
    step = {"name": "migrate a.xml", "klass": "core",
            "cmd": _py(f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); "
                       "print('SALIDA' + '_DEL_HIJO', flush=True); time.sleep(30)")}
    monkeypatch.setitem(rm._RUN, "dir", str(tmp_path))

    class SecondCtrlC(sp.Popen):
        def wait(self, timeout=None):
            if timeout == 0.25:              # la espera de gracia tras el primer Ctrl+C
                raise KeyboardInterrupt
            return super().wait(timeout)

    class Interrupting:
        def write(self, text):
            if "SALIDA_DEL_HIJO" in text:
                raise KeyboardInterrupt

        def flush(self):
            pass

    monkeypatch.setattr(rm.subprocess, "Popen", SecondCtrlC)
    monkeypatch.setattr(rm.sys, "stdout", Interrupting())
    with pytest.raises(KeyboardInterrupt):
        rm._run_step(step)
    monkeypatch.undo()
    pid = int(pid_file.read_text())
    time.sleep(0.3)
    try:
        os.kill(pid, 0)
        alive = True
        os.kill(pid, 9)
    except ProcessLookupError:
        alive = False
    assert not alive


def test_el_latido_no_se_pega_al_bloque_de_un_carril_sin_salto_final(tmp_path, capfd):
    stages = [{"kind": "par", "title": "t", "lanes": [
        {"lane": "A", "weight": 2, "steps": [{"name": "a", "klass": "core",
                                              "cmd": _py("import sys; sys.stdout.write('borrando 120/400 tablas...')")}]},
        {"lane": "B", "weight": 1, "steps": [{"name": "b", "klass": "core",
                                              "cmd": _py("import time; time.sleep(0.8)")}]},
    ]}]
    rm.execute(stages, force=False, jobs=2, log_dir=str(tmp_path), heartbeat_s=0.1)
    out = capfd.readouterr().out
    assert "⏱" in out and "tablas...⏱" not in out


def test_si_el_paso_no_puede_arrancar_su_log_no_queda_abierto(tmp_path, monkeypatch):
    import gc
    import warnings

    monkeypatch.setitem(rm._RUN, "dir", str(tmp_path))
    step = {"name": "x", "klass": "core", "cmd": [str(tmp_path / "no-existe")]}
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with pytest.raises(OSError):
            rm._run_step(step, "P")
        gc.collect()
    assert not [w for w in caught if issubclass(w.category, ResourceWarning)]
