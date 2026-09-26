"""Doc 95 D11: el seed describe la plantilla QA_MODELO (dry-run legible) y el
one-shot lo corre justo después de seed_upload_profiles."""
from __future__ import annotations

from app.features.reporting.sheet_templates.builtin import QA_MODELO
from scripts.run_migration import oneshot_stages
from scripts.seed_sheet_templates import plan_lines


def test_plan_lines_nombra_la_hoja_y_cada_columna_con_su_dato():
    lines = plan_lines(QA_MODELO)
    assert "hoja «QA_MODELO» · 14 columnas" in lines[0]
    assert any("UDP_DOMINIO_PRINCIPAL" in ln and "table.udp:Dominio Principal" in ln for ln in lines)


def test_oneshot_siembra_plantillas_tras_los_perfiles():
    plans = [{"project": "P", "path": "a.xml", "rel": "a.xml", "size": 1}]
    cierre = oneshot_stages(plans, force=False, base_title="v1")[-1]["steps"]
    names = [s["name"] for s in cierre]
    i_pf = next(i for i, n in enumerate(names) if "seed_upload_profiles" in n)
    i_st = next(i for i, n in enumerate(names) if "seed_sheet_templates" in n)
    assert i_st == i_pf + 1 and cierre[i_st]["klass"] == "core"
    assert cierre[i_st]["cmd"][-3:] == ["scripts.seed_sheet_templates", "--all-projects", "--apply"]


def test_seed_one_siembra_la_qa_modelo_compartida_y_no_repite(monkeypatch, capsys):
    """Final review #1: la sembrada por el one-shot es COMPARTIDA (la ven todos) y
    su dueño es `system` (la gobierna un admin); re-correr no la duplica."""
    import asyncio

    from app.core.db import client as db_client
    from scripts.seed_sheet_templates import seed_one
    from tests.support.fakedb import FakeDb

    fake = FakeDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    asyncio.run(seed_one("p1", "P", apply=True))
    asyncio.run(seed_one("p1", "P", apply=True))
    docs = list(fake.raw["sheet_templates"].find({}))
    assert len(docs) == 1 and docs[0]["shared"] is True and docs[0]["owner"] == "system"
    assert "SALTADO" in capsys.readouterr().out
