"""Lógica pura del orquestador run_migration (doc 54; manifiesto por proyecto
doc 75 D9) — sin BD ni subprocesos."""
from __future__ import annotations

from pathlib import Path

import pytest

import scripts.run_migration as rm
from scripts.run_migration import (
    append_steps, discover_xmls, oneshot_steps, parse_locator, plan_files, read_locator,
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


def _touch(root, *rels):
    out = []
    for rel in rels:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
        out.append(p)
    return out


def test_glob_re_estrella_no_cruza_carpetas_y_doble_estrella_si():
    rx = rm._glob_re("Modelo DDV/*.xml")
    assert rx.match("modelo ddv/a.XML") and not rx.match("Modelo DDV/sub/a.xml")
    assert rm._glob_re("**/x.xml").match("a/b/x.xml") and rm._glob_re("**/x.xml").match("x.xml")


def test_load_manifest_ausente_estricto_y_nombres_unicos(tmp_path):
    assert rm.load_manifest(None) == {"projects": []}
    assert rm.load_manifest(tmp_path / "projects.json") == {"projects": []}
    m = tmp_path / "projects.json"
    m.write_text('{"projects": [{"name": "A", "files": ["a/*.xml"], "typo": 1}]}')
    with pytest.raises(rm.ManifestError, match="typo"):
        rm.load_manifest(m)
    m.write_text('{"projects": [{"name": " ", "files": ["a/*.xml"]}]}')
    with pytest.raises(rm.ManifestError, match="name"):
        rm.load_manifest(m)
    m.write_text('{"projects": [{"name": "A", "files": ["a/*.xml"]}, {"name": "a", "files": ["b/*.xml"]}]}')
    with pytest.raises(rm.ManifestError, match="repetido"):
        rm.load_manifest(m)
    m.write_text('{"projects": [{"name": "A", "files": []}]}')
    with pytest.raises(rm.ManifestError, match="files"):
        rm.load_manifest(m)
    m.write_text("{not json")
    with pytest.raises(rm.ManifestError, match="JSON"):
        rm.load_manifest(m)


def test_plan_files_manifiesto_manda_y_regla_general_nombre_de_archivo(tmp_path):
    files = _touch(tmp_path, "Modelo de Datos DDV_FISICO/DDV - CPYBCA.xml",
                   "Modelo de Datos DDV_FISICO/DDV Otros V0.214.xml",
                   "UDV int Fisico/UDV INT FISICO.xml", "UDV int Logico/UDV INT LOGICO.xml")
    manifest = {"projects": [{"name": "Modelo DDV", "files": ["Modelo de Datos DDV_FISICO/*.xml"],
                              "description": "DDV físico"}]}
    locs = {"DDV - CPYBCA.xml": "Mart://Mart/Modelo DDV/CPYBCA/DDV CPYBCA",
            "UDV INT FISICO.xml": "Mart://Mart/Modelo UDV/Modelo Fisico/UDV F"}
    plans = plan_files(files, tmp_path, manifest, locator_of=lambda p: locs.get(p.name))
    assert [p["project"] for p in plans] == ["Modelo DDV", "Modelo DDV", "UDV INT FISICO", "UDV INT LOGICO"]
    assert [p["source"] for p in plans] == ["manifest", "manifest", "archivo", "archivo"]
    assert [p["domain"] for p in plans] == ["CPYBCA", "", "Modelo Fisico", ""]
    assert plans[0]["description"] == "DDV físico" and plans[2]["description"] is None
    assert plans[0]["rel"] == "Modelo de Datos DDV_FISICO/DDV - CPYBCA.xml"
    assert [(g, len(fs)) for g, fs in rm.group_by_project(plans)] == [
        ("Modelo DDV", 2), ("UDV INT FISICO", 1), ("UDV INT LOGICO", 1)]


def test_plan_files_rechaza_patron_sin_match_doble_match_y_nombres_repetidos(tmp_path):
    files = _touch(tmp_path, "a/x.xml", "b/y.xml")
    none = lambda p: None  # noqa: E731
    with pytest.raises(rm.ManifestError, match="no matchea"):
        plan_files(files, tmp_path, {"projects": [{"name": "P", "files": ["c/*.xml"]}]}, locator_of=none)
    with pytest.raises(rm.ManifestError, match="dos entradas"):
        plan_files(files, tmp_path, {"projects": [{"name": "P", "files": ["a/*.xml"]},
                                                  {"name": "Q", "files": ["**/x.xml"]}]}, locator_of=none)
    with pytest.raises(rm.ManifestError, match="repetido"):
        plan_files(files, tmp_path, {"projects": [{"name": "y", "files": ["a/*.xml"]}]}, locator_of=none)
    with pytest.raises(rm.ManifestError, match="repetido"):
        plan_files(_touch(tmp_path, "c/z.xml", "d/z.xml"), tmp_path, {"projects": []}, locator_of=none)


# ── Construcción de pasos ────────────────────────────────────────────────────

def _plan(rel: str, project: str, description: str | None = None) -> dict:
    return {"path": Path("/x") / rel, "rel": rel, "project": project, "description": description,
            "domain": "", "model": project, "source": "manifest"}


def test_oneshot_steps_quality_por_proyecto_y_comandos():
    steps = oneshot_steps([_plan("a.xml", "PA", "desc A"), _plan("b.xml", "PA", "desc A"), _plan("c.xml", "PB")],
                          force=True, base_title="Mi Base", quality_json="/r/q.json")
    assert [s["klass"] for s in steps] == ["gate", "gate", "abort", "abort", "core", "core", "core",
                                           "check", "core", "info", "core"]
    # un gate por PROYECTO (glosario cruzado sólo entre los archivos que se unen)
    assert steps[0]["cmd"][-4:] == ["--json", "/r/q-1.json", str(Path("/x/a.xml")), str(Path("/x/b.xml"))]
    assert steps[1]["cmd"][-3:] == ["--json", "/r/q-2.json", str(Path("/x/c.xml"))]
    assert steps[2]["cmd"][-2:] == ["scripts.reset_for_migration", "--apply"]
    assert steps[3]["cmd"][-1] == "scripts/create_admin.py"
    assert steps[4]["cmd"][-6:] == ["--project", "PA", "--description", "desc A", "--apply", "--force"]
    assert steps[6]["cmd"][-4:] == ["--project", "PB", "--apply", "--force"]
    assert steps[8]["cmd"][-3:] == ["scripts.seed_ddl_export_rules", "--all-projects", "--apply"]
    assert steps[-1]["cmd"][-2:] == ["--title", "Mi Base"]


def test_quality_json_va_antes_de_los_xml():
    assert append_steps(_plan("m.xml", "P"), force=False, quality_json="/r/q.json")[0]["cmd"][-3:-1] == ["--json", "/r/q.json"]


def test_gate_error_summary_solo_errores_con_archivo_y_cross_file():
    report = [
        {"file": "/x/UDV Fisico.xml", "summary": {}, "findings": [
            {"code": "W-X", "severity": "WARN", "title": "aviso", "action": "a", "count": 1, "items": ["i"], "samples": ["i"]},
            {"code": "E-REL-BROKEN", "severity": "ERROR", "title": "Relaciones con tabla/vista inexistente",
             "action": "la migración las OMITE — confirmar", "count": 7,
             "items": [f"R/{i}" for i in range(7)], "samples": [f"R/{i}" for i in range(7)]},
        ]},
        {"crossFile": {"glossaryConflicts": [{"term": "CODIGO", "byFile": {"a": "COD", "b": "CD"}}]}},
    ]
    lines = rm.gate_error_summary(report, max_items=5)
    assert lines[0] == "  [ERROR] E-REL-BROKEN — Relaciones con tabla/vista inexistente  (7) · UDV Fisico.xml"
    assert lines[1].strip() == "acción: la migración las OMITE — confirmar"
    assert lines[2:7] == [f"          · R/{i}" for i in range(5)] and lines[7].strip() == "· … y 2 más"
    assert lines[8].startswith("  [ERROR] E-GLOSSARY-XFILE-CONFLICT") and lines[9].strip() == "· CODIGO"
    assert "W-X" not in "\n".join(lines)


def test_oneshot_sin_force_no_lo_propaga():
    steps = oneshot_steps([_plan("a.xml", "PA")], force=False, base_title="T")
    assert steps[3]["cmd"][-3:] == ["--project", "PA", "--apply"]
    assert all("--force" not in s["cmd"] for s in steps)


def test_append_steps_forma():
    steps = append_steps(_plan("m.xml", "Proy"), force=False)
    assert [s["klass"] for s in steps] == ["gate", "info", "core", "check", "info"]
    assert steps[1]["cmd"][-3:] == ["--project", "Proy", str(Path("/x/m.xml"))]     # crosscheck --project
    assert steps[2]["cmd"][-3:] == ["--project", "Proy", "--apply"]
    assert steps[4]["cmd"][-2:] == ["--project", "Proy"]


def test_main_aborta_con_manifiesto_invalido_antes_de_tocar_la_bd(tmp_path, monkeypatch, capsys):
    _touch(tmp_path, "a/x.xml")
    (tmp_path / "projects.json").write_text('{"projects": [{"name": "P", "files": ["zzz/*.xml"]}]}')
    monkeypatch.setattr(rm, "execute", lambda *a, **k: pytest.fail("no debe ejecutar nada"))
    monkeypatch.setattr(rm, "_run_step", lambda *a, **k: pytest.fail("no debe ejecutar nada"))
    assert rm.main(["--folder", str(tmp_path)]) == 2
    assert "no matchea" in capsys.readouterr().out


def test_main_manifiesto_explicito_inexistente(tmp_path, monkeypatch, capsys):
    _touch(tmp_path, "a/x.xml")
    monkeypatch.setattr(rm, "execute", lambda *a, **k: pytest.fail("no debe ejecutar nada"))
    monkeypatch.setattr(rm, "_run_step", lambda *a, **k: pytest.fail("no debe ejecutar nada"))
    assert rm.main(["--folder", str(tmp_path), "--manifest", str(tmp_path / "nope.json")]) == 2
    assert "No existe el manifiesto" in capsys.readouterr().out


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
