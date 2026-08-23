"""Lógica pura del orquestador run_migration (doc 54) — sin BD ni subprocesos."""
from __future__ import annotations

from pathlib import Path

import scripts.run_migration as rm
from scripts.run_migration import (
    append_steps, discover_xmls, filename_collisions, oneshot_steps,
    parse_locator, plan_files, read_locator,
)

LOC = ("erwin://Mart://Mart/Modelo UDV/Modelo Logico/"
       "UDV Modelo de Datos Logico V0.618"
       "?&version=1&modelLongId={92F27D5C}+00000000")


# ── Locator del Mart ─────────────────────────────────────────────────────────

def test_parse_locator_tres_capas_y_query():
    assert parse_locator(LOC) == {
        "project": "Modelo UDV", "domain": "Modelo Logico",
        "model": "UDV Modelo de Datos Logico V0.618"}


def test_parse_locator_sin_dominio_y_anidado():
    assert parse_locator("Mart://Mart/P/M")["domain"] == ""
    assert parse_locator("Mart://Mart/P/a/b/M")["domain"] == "a / b"


def test_parse_locator_no_mart_o_incompleto():
    assert parse_locator("erwin://file://C:/modelos/x.erwin") is None
    assert parse_locator("Mart://Mart/SoloProyecto") is None


def test_read_locator_encuentra_desescapa_y_respeta_tope(tmp_path):
    f = tmp_path / "a.xml"
    f.write_bytes(b"<x>" + b"z" * 100 +
                  b"<Locator>Mart://Mart/P/D/Mo&amp;delo</Locator><y>")
    assert read_locator(f) == "Mart://Mart/P/D/Mo&delo"
    assert read_locator(f, cap=50) is None
    assert read_locator(tmp_path / "a.xml", _chunk=16) == "Mart://Mart/P/D/Mo&delo"

    g = tmp_path / "b.xml"
    g.write_bytes(b"<x>sin locator</x>")
    assert read_locator(g) is None


# ── Descubrimiento y plan ────────────────────────────────────────────────────

def test_discover_recursivo_orden_determinista_e_ignora_no_xml(tmp_path):
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "sub").mkdir(parents=True)
    (tmp_path / "b" / "Uno.xml").write_text("x")
    (tmp_path / "a" / "sub" / "dos.XML").write_text("x")
    (tmp_path / "a" / "notas.txt").write_text("x")
    (tmp_path / "raiz.xml").write_text("x")
    got = [str(p.relative_to(tmp_path)) for p in discover_xmls(tmp_path)]
    assert got == ["a/sub/dos.XML", "b/Uno.xml", "raiz.xml"]


def test_plan_files_locator_manda_y_fallback_nombre(tmp_path):
    con = tmp_path / "sub" / "con_mart.xml"
    sin = tmp_path / "Sin Mart V1.xml"
    con.parent.mkdir()
    con.write_text("x")
    sin.write_text("x")
    plans = plan_files(
        [con, sin], tmp_path,
        locator_of=lambda p: LOC if p.name == "con_mart.xml" else None)
    assert plans[0]["project"] == "Modelo UDV"
    assert plans[0]["source"] == "locator"
    assert plans[0]["domain"] == "Modelo Logico"
    assert plans[0]["rel"] == "sub/con_mart.xml"
    assert plans[1]["project"] == "Sin Mart V1"
    assert plans[1]["source"] == "archivo"


def test_filename_collisions_solo_entre_derivados_de_archivo():
    plans = [
        {"rel": "a/m.xml", "project": "M", "source": "archivo"},
        {"rel": "b/M.xml", "project": "M", "source": "archivo"},
        {"rel": "c.xml", "project": "M", "source": "locator"},
        {"rel": "d.xml", "project": "Otro", "source": "archivo"},
    ]
    assert filename_collisions(plans) == {"m": ["a/m.xml", "b/M.xml"]}


# ── Construcción de pasos ────────────────────────────────────────────────────

def _plan(rel: str, project: str) -> dict:
    return {"path": Path("/x") / rel, "rel": rel, "project": project,
            "domain": "", "model": project, "source": "locator"}


def test_oneshot_steps_orden_comandos_y_force():
    steps = oneshot_steps([_plan("a.xml", "PA"), _plan("b.xml", "PB")],
                          force=True, base_title="Mi Base")
    assert [s["klass"] for s in steps] == [
        "gate", "abort", "abort", "core", "core", "check", "core", "info", "core"]
    assert steps[0]["cmd"][-2:] == [str(Path("/x/a.xml")), str(Path("/x/b.xml"))]
    assert steps[1]["cmd"][-2:] == ["scripts.reset_for_migration", "--apply"]
    assert steps[2]["cmd"][-1] == "scripts/create_admin.py"
    assert steps[3]["cmd"][-4:] == ["--project", "PA", "--apply", "--force"]
    assert steps[4]["cmd"][-4:] == ["--project", "PB", "--apply", "--force"]
    assert steps[-1]["cmd"][-2:] == ["--title", "Mi Base"]


def test_oneshot_sin_force_no_lo_propaga():
    steps = oneshot_steps([_plan("a.xml", "PA")], force=False, base_title="T")
    assert steps[3]["cmd"][-3:] == ["--project", "PA", "--apply"]
    assert "--force" not in steps[0]["cmd"]


def test_append_steps_forma():
    steps = append_steps(_plan("m.xml", "Proy"), force=False)
    assert [s["klass"] for s in steps] == ["gate", "info", "core", "check", "info"]
    assert steps[2]["cmd"][-3:] == ["--project", "Proy", "--apply"]
    assert steps[4]["cmd"][-2:] == ["--project", "Proy"]


# ── Política de fallas ───────────────────────────────────────────────────────

def test_execute_abort_detiene_y_omite_el_resto(monkeypatch):
    rcs = {"gate": 0, "abort": 1, "core": 0}
    monkeypatch.setattr(rm, "_run_step", lambda st: (rcs[st["klass"]], 0.0))
    steps = [{"name": "g", "klass": "gate", "cmd": []},
             {"name": "a", "klass": "abort", "cmd": []},
             {"name": "c", "klass": "core", "cmd": []}]
    res = rm.execute(steps, force=False)
    assert [r["estado"] for r in res] == ["OK", "ERROR — DETIENE", "OMITIDO"]
    assert res[1]["fatal"] and not res[2]["fatal"]


def test_execute_gate_sin_force_detiene_con_force_continua(monkeypatch):
    monkeypatch.setattr(
        rm, "_run_step", lambda st: (1 if st["klass"] == "gate" else 0, 0.0))
    steps = [{"name": "g", "klass": "gate", "cmd": []},
             {"name": "m", "klass": "core", "cmd": []}]

    res = rm.execute(steps, force=False)
    assert res[0]["estado"] == "ERROR — DETIENE (gate)" and res[0]["fatal"]
    assert res[1]["estado"] == "OMITIDO"

    res = rm.execute(steps, force=True)
    assert res[0]["estado"] == "CON ERRORS (--force: continúa)"
    assert not res[0]["fatal"]
    assert res[1]["estado"] == "OK"


def test_execute_core_falla_visible_pero_continua_e_info_solo_warning(monkeypatch):
    monkeypatch.setattr(
        rm, "_run_step", lambda st: (1 if st["klass"] in ("core", "info") else 0, 0.0))
    steps = [{"name": "m", "klass": "core", "cmd": []},
             {"name": "aud", "klass": "check", "cmd": []},
             {"name": "arr", "klass": "info", "cmd": []}]
    res = rm.execute(steps, force=False)
    assert [r["estado"] for r in res] == [
        "ERROR", "OK", "WARNING (informativo)"]
    assert res[0]["fatal"] and not res[2]["fatal"]
