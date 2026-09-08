"""Las features legacy (superadas por el modelo canónico de M1/M3) ya no existen."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FEATURES = ROOT / "app" / "features"


def test_legacy_features_removed():
    # `excel_import` (2026-07): su único consumidor era el flujo de import del
    # frontend viejo (editor/table-editor), eliminado en la limpieza de código
    # muerto. La carga masiva de Erwin va por script directo a la DB.
    for name in ("canvas", "metadata", "excel_import"):
        assert not (FEATURES / name).exists(), f"feature legacy '{name}' aún existe"


def test_legacy_backend_trees_removed():
    """`api/` + `src/` eran el backend PRE-refactor completo (auth, canvas,
    metadata, project-centric). El composition root vigente es `app/main.py`;
    estos árboles se eliminaron en la limpieza 2026-07 y no deben volver."""
    for name in ("api", "src"):
        assert not (ROOT / name).exists(), f"árbol legacy '{name}/' aún existe"


def test_projects_is_the_new_one():
    # El projects nuevo no tiene `canvas.py`; el viejo router montaba el canvas.
    text = (FEATURES / "projects" / "models.py").read_text(encoding="utf-8")
    assert "ModelLevelDoc" not in text, "projects sigue siendo el legacy (tiene ModelLevelDoc)"


def test_legacy_scripts_y_rutas_retirados():
    """Doc 75 D14: backfills superados por el one-shot (regla del owner: nada de
    backfill/refix) y endpoints sin consumidor en el front."""
    for name in ("backfill_canvas_views", "backfill_schema_kind", "backfill_udp_view",
                 "homologate_domain_types", "purge_invalid_relationships", "seed_view_type_udp"):
        assert not (ROOT / "scripts" / f"{name}.py").exists(), f"script legacy '{name}' aún existe"
    identity = (FEATURES / "identity" / "router.py").read_text(encoding="utf-8")
    assert '"/me"' not in identity, "GET /api/me duplica /api/auth/me"
    glossary = (FEATURES / "glossary" / "router.py").read_text(encoding="utf-8")
    assert "logicalize" not in glossary
    projects = (FEATURES / "projects" / "router.py").read_text(encoding="utf-8")
    assert "/udp" not in projects and "UdpValuesBody" not in projects
