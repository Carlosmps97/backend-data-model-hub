"""Doc 78 §9: el seed describe el perfil resuelto (dry-run legible) y el
one-shot lo corre justo después de seed_ddl_export_rules."""
from __future__ import annotations

from app.features.bulk_upload.profiles.builtin import PLANTILLA_BCP, materialize
from scripts.erwin_migration.standard_udps import FIXED_UDPS
from scripts.run_migration import oneshot_stages
from scripts.seed_upload_profiles import plan_lines

DEFS = [{**d, "id": f"u{i}"} for i, d in enumerate(FIXED_UDPS)]


def test_plan_lines_nombra_cabeceras_y_udp_resueltos():
    body, warnings = materialize(PLANTILLA_BCP, DEFS)
    lines = plan_lines(body, DEFS)
    assert any("Cargar_Tablas" in ln and "fila 5" in ln for ln in lines)
    assert any("UDP_Tipo_de_Entidad" in ln and "Tipo de Entidad (physical)" in ln and "Tipo de Entidad (logical)" in ln for ln in lines)
    assert any("LOGICO" in ln and "ignore" in ln for ln in lines)
    assert any("TABLA_LOGICO" in ln and "required, maxLength=80" in ln for ln in lines)


def test_oneshot_siembra_perfiles_tras_las_reglas_ddl():
    plans = [{"project": "P", "path": "a.xml", "rel": "a.xml", "size": 1}]
    stages = oneshot_stages(plans, force=False, base_title="v1")
    cierre = stages[-1]["steps"]
    names = [s["name"] for s in cierre]
    i_ddl = next(i for i, n in enumerate(names) if "seed_ddl_export_rules" in n)
    i_pf = next(i for i, n in enumerate(names) if "seed_upload_profiles" in n)
    assert i_pf == i_ddl + 1 and cierre[i_pf]["klass"] == "core"
    assert cierre[i_pf]["cmd"][-3:] == ["scripts.seed_upload_profiles", "--all-projects", "--apply"]
