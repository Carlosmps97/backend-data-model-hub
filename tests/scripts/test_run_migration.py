"""Lógica pura del orquestador run_migration (doc 54; proyectos por CONVENCIÓN y
carriles paralelos doc 77) — sin BD ni subprocesos reales."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

import scripts.run_migration as rm
from scripts.run_migration import (
    append_stages, discover_xmls, execute, oneshot_stages, parse_locator,
    plan_files, project_of, read_locator,
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


# ── Descubrimiento y convención de proyectos (doc 77 §3) ─────────────────────

def test_discover_recursivo_orden_determinista_e_ignora_no_xml(tmp_path):
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "sub").mkdir(parents=True)
    (tmp_path / "b" / "Uno.xml").write_text("x")
    (tmp_path / "a" / "sub" / "dos.XML").write_text("x")
    (tmp_path / "a" / "notas.txt").write_text("x")
    (tmp_path / "raiz.xml").write_text("x")
    got = [str(p.relative_to(tmp_path)) for p in discover_xmls(tmp_path)]
    assert got == ["a/sub/dos.XML", "b/Uno.xml", "raiz.xml"]


def test_project_of_subcarpeta_manda_y_archivo_suelto_usa_su_nombre():
    assert project_of("MODELO DDV/DDV - CPYBCA.xml") == "MODELO DDV"
    assert project_of("MODELO DDV/sub/otro.xml") == "MODELO DDV"   # a cualquier profundidad
    assert project_of("UDV INT FISICO.xml") == "UDV INT FISICO"
    assert project_of("UDV INT FISICO.XML") == "UDV INT FISICO"    # extensión en mayúscula


def _touch(root, *rels, size: int = 1):
    out = []
    for rel in rels:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x" * size)
        out.append(p)
    return out


def test_plan_files_fusiona_la_subcarpeta_y_deja_sueltos_como_proyecto_propio(tmp_path):
    files = _touch(tmp_path, "MODELO DDV/DDV - CPYBCA.xml",
                   "MODELO DDV/DDV Otros V0.214.xml",
                   "UDV INT FISICO.xml", "UDV INT LOGICO.xml")
    locs = {"DDV - CPYBCA.xml": "Mart://Mart/Modelo DDV/CPYBCA/DDV CPYBCA",
            "UDV INT FISICO.xml": "Mart://Mart/Modelo UDV/Modelo Fisico/UDV F"}
    plans = plan_files(files, tmp_path, locator_of=lambda p: locs.get(p.name))

    assert [p["project"] for p in plans] == [
        "MODELO DDV", "MODELO DDV", "UDV INT FISICO", "UDV INT LOGICO"]
    assert [p["source"] for p in plans] == ["carpeta", "carpeta", "archivo", "archivo"]
    assert [p["domain"] for p in plans] == ["CPYBCA", "", "Modelo Fisico", ""]
    assert plans[0]["rel"] == "MODELO DDV/DDV - CPYBCA.xml"
    assert [(g, len(fs)) for g, fs in rm.group_by_project(plans)] == [
        ("MODELO DDV", 2), ("UDV INT FISICO", 1), ("UDV INT LOGICO", 1)]


def test_plan_files_rechaza_carpeta_y_archivo_suelto_con_el_mismo_nombre(tmp_path):
    files = _touch(tmp_path, "MODELO DDV/a.xml", "Modelo DDV.xml")
    with pytest.raises(rm.DiscoveryError, match="MODELO DDV"):
        plan_files(files, tmp_path, locator_of=lambda p: None)


def test_plan_files_rechaza_dos_sueltos_que_solo_difieren_en_mayusculas(tmp_path):
    files = [tmp_path / "Modelo.xml", tmp_path / "MODELO.xml"]
    for f in files:
        f.write_text("x")
    with pytest.raises(rm.DiscoveryError, match="repetido"):
        plan_files(files, tmp_path, locator_of=lambda p: None)


def test_plan_files_lleva_el_tamano_de_cada_archivo(tmp_path):
    files = _touch(tmp_path, "a.xml", size=7)
    assert plan_files(files, tmp_path, locator_of=lambda p: None)[0]["size"] == 7


# ── Etapas y carriles (doc 77 §4) ────────────────────────────────────────────

def _plan(rel: str, project: str, size: int = 1, source: str = "carpeta") -> dict:
    return {"path": Path("/x") / rel, "rel": rel, "project": project, "size": size,
            "domain": "", "model": project, "source": source}


def test_oneshot_stages_un_carril_por_proyecto_entre_reset_y_cierre():
    stages = oneshot_stages(
        [_plan("a.xml", "PA"), _plan("b.xml", "PA"), _plan("c.xml", "PB")],
        force=True, base_title="Mi Base", quality_json="/r/q.json")

    assert [s["kind"] for s in stages] == ["par", "seq", "par", "seq"]

    gates = stages[0]["lanes"]
    assert [g["lane"] for g in gates] == ["PA", "PB"]
    assert [st["klass"] for g in gates for st in g["steps"]] == ["gate", "gate"]
    # un gate por PROYECTO (glosario cruzado sólo entre los archivos que se unen)
    assert gates[0]["steps"][0]["cmd"][-4:] == [
        "--json", "/r/q-1.json", str(Path("/x/a.xml")), str(Path("/x/b.xml"))]
    assert gates[1]["steps"][0]["cmd"][-3:] == ["--json", "/r/q-2.json", str(Path("/x/c.xml"))]

    assert [st["klass"] for st in stages[1]["steps"]] == ["abort", "abort"]
    assert stages[1]["steps"][0]["cmd"][-2:] == ["scripts.reset_for_migration", "--apply"]
    assert stages[1]["steps"][1]["cmd"][-1] == "scripts/create_admin.py"

    migr = stages[2]["lanes"]
    assert [m["lane"] for m in migr] == ["PA", "PB"]
    assert [st["klass"] for m in migr for st in m["steps"]] == ["core", "core", "core"]
    # dentro del carril, los archivos del proyecto conservan su orden
    assert [st["cmd"][3] for st in migr[0]["steps"]] == [str(Path("/x/a.xml")),
                                                         str(Path("/x/b.xml"))]
    assert migr[0]["steps"][0]["cmd"][-4:] == ["--project", "PA", "--apply", "--force"]

    cierre = stages[3]["steps"]
    assert [st["klass"] for st in cierre] == ["check", "core", "core", "info", "core"]
    assert cierre[2]["cmd"][-3:] == ["scripts.seed_upload_profiles", "--all-projects", "--apply"]
    assert cierre[1]["cmd"][-3:] == ["scripts.seed_ddl_export_rules", "--all-projects", "--apply"]
    assert cierre[-1]["cmd"][-2:] == ["--title", "Mi Base"]


def test_oneshot_sin_force_no_lo_propaga():
    stages = oneshot_stages([_plan("a.xml", "PA")], force=False, base_title="T")
    steps = [st for s in stages for st in rm._stage_steps(s)]   # gate, reset, admin, migrate…
    assert steps[3]["cmd"][-3:] == ["--project", "PA", "--apply"]
    assert all("--force" not in st["cmd"] for st in steps)


def test_carril_pesa_la_suma_de_sus_archivos_y_despacha_de_mayor_a_menor():
    stages = oneshot_stages([_plan("a.xml", "PA", size=10), _plan("b.xml", "PA", size=5),
                             _plan("c.xml", "PB", size=40)],
                            force=False, base_title="T")
    lanes = stages[2]["lanes"]
    assert [l["weight"] for l in lanes] == [15, 40]      # orden natural = orden de proyectos
    assert rm._dispatch_order(lanes) == [1, 0]           # el pesado arranca primero


def test_append_stages_es_secuencial_de_punta_a_punta():
    stages = append_stages(_plan("m.xml", "Proy", source="archivo"), force=False)
    assert [s["kind"] for s in stages] == ["seq"]
    steps = stages[0]["steps"]
    assert [st["klass"] for st in steps] == ["gate", "info", "core", "check", "info"]
    assert steps[1]["cmd"][-3:] == ["--project", "Proy", str(Path("/x/m.xml"))]   # crosscheck
    assert steps[2]["cmd"][-3:] == ["--project", "Proy", "--apply"]
    assert steps[4]["cmd"][-2:] == ["--project", "Proy"]


def test_quality_json_va_antes_de_los_xml():
    steps = append_stages(_plan("m.xml", "P"), force=False, quality_json="/r/q.json")[0]["steps"]
    assert steps[0]["cmd"][-3:-1] == ["--json", "/r/q.json"]


# ── Ejecución: política de fallas (secuencial) ───────────────────────────────

def _seq(*klasses) -> list[dict]:
    return [{"kind": "seq", "title": "t",
             "steps": [{"name": k, "klass": k, "cmd": []} for k in klasses]}]


def test_execute_abort_detiene_y_omite_el_resto(monkeypatch):
    rcs = {"gate": 0, "abort": 1, "core": 0}
    monkeypatch.setattr(rm, "_run_step", lambda st, lane=None: (rcs[st["klass"]], 0.0))
    res = execute(_seq("gate", "abort", "core"), force=False)
    assert [r["estado"] for r in res] == ["OK", "ERROR — DETIENE", "OMITIDO"]
    assert res[1]["fatal"] and not res[2]["fatal"]


def test_execute_gate_sin_force_detiene_con_force_continua(monkeypatch):
    monkeypatch.setattr(rm, "_run_step",
                        lambda st, lane=None: (1 if st["klass"] == "gate" else 0, 0.0))
    res = execute(_seq("gate", "core"), force=False)
    assert res[0]["estado"] == "ERROR — DETIENE (gate)" and res[0]["fatal"]
    assert res[1]["estado"] == "OMITIDO"

    res = execute(_seq("gate", "core"), force=True)
    assert res[0]["estado"] == "CON ERRORS (--force: continúa)"
    assert not res[0]["fatal"] and res[1]["estado"] == "OK"


def test_execute_core_falla_visible_pero_continua_e_info_solo_warning(monkeypatch):
    monkeypatch.setattr(
        rm, "_run_step",
        lambda st, lane=None: (1 if st["klass"] in ("core", "info") else 0, 0.0))
    res = execute(_seq("core", "info", "check"), force=False)
    assert [r["estado"] for r in res] == ["ERROR", "WARNING (informativo)", "OK"]
    assert res[0]["fatal"] and not res[1]["fatal"]


# ── Ejecución: carriles paralelos (doc 77 §4) ────────────────────────────────

def _par(*lanes) -> dict:
    """`_par(("PA", 10, ["core", "core"]), …)` → etapa paralela lista para execute."""
    return {"kind": "par", "title": "t", "lanes": [
        {"lane": name, "weight": w,
         "steps": [{"name": f"{name}-{i}", "klass": k, "cmd": []}
                   for i, k in enumerate(klasses)]}
        for name, w, klasses in lanes]}


def test_execute_paralelo_corre_los_carriles_a_la_vez(monkeypatch):
    """Sin concurrencia real la barrera expira y el paso devuelve rc=1."""
    barrera = threading.Barrier(2, timeout=5)

    def corre(st, lane=None):
        try:
            barrera.wait()
        except threading.BrokenBarrierError:
            return 1, 0.0
        return 0, 0.0

    monkeypatch.setattr(rm, "_run_step", corre)
    res = execute([_par(("PA", 1, ["core"]), ("PB", 1, ["core"]))], force=False, jobs=2)
    assert [r["estado"] for r in res] == ["OK", "OK"]


def test_execute_paralelo_reporta_en_orden_de_carril_no_de_llegada(monkeypatch):
    orden_fin: list[str] = []

    def corre(st, lane=None):
        if lane == "PA":                      # PA termina último
            threading.Event().wait(0.05)
        orden_fin.append(st["name"])
        return 0, 0.0

    monkeypatch.setattr(rm, "_run_step", corre)
    res = execute([_par(("PA", 1, ["core"]), ("PB", 1, ["core"]))], force=False, jobs=2)
    assert orden_fin == ["PB-0", "PA-0"]
    assert [r["name"] for r in res] == ["PA-0", "PB-0"]
    assert [r["lane"] for r in res] == ["PA", "PB"]


def test_execute_paralelo_conserva_el_orden_dentro_del_carril(monkeypatch):
    vistos: list[str] = []
    monkeypatch.setattr(rm, "_run_step",
                        lambda st, lane=None: (vistos.append(st["name"]), (0, 0.0))[1])
    execute([_par(("PA", 1, ["core", "core", "core"]))], force=False, jobs=4)
    assert vistos == ["PA-0", "PA-1", "PA-2"]


def test_execute_paralelo_gate_en_rojo_deja_terminar_los_carriles_y_luego_aborta(monkeypatch):
    corridos: list[str] = []

    def corre(st, lane=None):
        corridos.append(st["name"])
        return (1 if lane == "PA" else 0), 0.0

    monkeypatch.setattr(rm, "_run_step", corre)
    stages = [_par(("PA", 2, ["gate"]), ("PB", 1, ["gate"])),
              {"kind": "seq", "title": "t", "steps": [{"name": "reset", "klass": "abort", "cmd": []}]}]
    res = execute(stages, force=False, jobs=2)
    assert sorted(corridos) == ["PA-0", "PB-0"]          # el otro gate SÍ corrió
    assert "reset" not in corridos                        # y la BD no se tocó
    assert [r["estado"] for r in res] == [
        "ERROR — DETIENE (gate)", "OK", "OMITIDO"]


def test_execute_paralelo_migrate_en_rojo_no_frena_al_resto(monkeypatch):
    monkeypatch.setattr(rm, "_run_step",
                        lambda st, lane=None: ((1 if lane == "PA" else 0), 0.0))
    stages = [_par(("PA", 2, ["core", "core"]), ("PB", 1, ["core"])),
              {"kind": "seq", "title": "t", "steps": [{"name": "audit", "klass": "check", "cmd": []}]}]
    res = execute(stages, force=False, jobs=2)
    assert [r["estado"] for r in res] == ["ERROR", "ERROR", "OK", "OK"]
    assert any(r["fatal"] for r in res)


def test_execute_con_jobs_1_usa_el_camino_secuencial_con_streaming(monkeypatch):
    lanes_vistos: list = []
    monkeypatch.setattr(rm, "_run_step",
                        lambda st, lane=None: (lanes_vistos.append(lane), (0, 0.0))[1])
    execute([_par(("PA", 1, ["core"]), ("PB", 1, ["core"]))], force=False, jobs=1)
    assert lanes_vistos == [None, None]     # sin carril = salida en vivo


# ── Resumen del gate ─────────────────────────────────────────────────────────

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


# ── main ─────────────────────────────────────────────────────────────────────

def test_main_aborta_con_nombres_en_colision_antes_de_tocar_la_bd(tmp_path, monkeypatch, capsys):
    _touch(tmp_path, "MODELO DDV/a.xml", "Modelo DDV.xml")
    monkeypatch.setattr(rm, "execute", lambda *a, **k: pytest.fail("no debe ejecutar nada"))
    monkeypatch.setattr(rm, "_run_step", lambda *a, **k: pytest.fail("no debe ejecutar nada"))
    assert rm.main(["--folder", str(tmp_path)]) == 2
    assert "MODELO DDV" in capsys.readouterr().out


def test_main_rechaza_jobs_menor_a_uno(tmp_path, monkeypatch):
    _touch(tmp_path, "a.xml")
    monkeypatch.setattr(rm, "execute", lambda *a, **k: pytest.fail("no debe ejecutar nada"))
    with pytest.raises(SystemExit):
        rm.main(["--folder", str(tmp_path), "--jobs", "0"])


def test_main_avisa_si_queda_un_projects_json_residual(tmp_path, monkeypatch, capsys):
    _touch(tmp_path, "a.xml")
    (tmp_path / "projects.json").write_text('{"projects": []}')
    monkeypatch.setattr(rm, "_run_step", lambda *a, **k: (0, 0.0))
    assert rm.main(["--folder", str(tmp_path)]) == 0
    assert "projects.json" in capsys.readouterr().out
