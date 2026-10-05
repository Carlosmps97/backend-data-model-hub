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


def test_oneshot_siembra_perfiles_en_el_cierre_antes_de_la_version_base():
    """Doc 109: los seeds del cierre corren en paralelo (no dependen entre sí:
    el perfil resuelve UDP del catálogo que siembra migrate) y la versión base
    va DESPUÉS de todos."""
    plans = [{"project": "P", "path": "a.xml", "rel": "a.xml", "size": 1}]
    stages = oneshot_stages(plans, force=False, base_title="v1")
    cierre = [st for lane in stages[-2]["lanes"] for st in lane["steps"]]
    pf = next(st for st in cierre if "seed_upload_profiles" in st["name"])
    assert pf["klass"] == "core"
    assert pf["cmd"][-3:] == ["scripts.seed_upload_profiles", "--all-projects", "--apply"]
    assert "mark_base_version" in stages[-1]["steps"][0]["name"]
