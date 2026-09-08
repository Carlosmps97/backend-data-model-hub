"""Doc 61: el camino CHANGESET valida el customSql al grabar (payload_error)
y RE-deriva customColumns en apply_plan (no se confía en el cliente)."""
from __future__ import annotations

from app.features.changesets import validation
from app.features.changesets.service import apply_plan


def _view_payload(**extra) -> dict:
    return {"id": "v1", "projectId": "p1", "name": "v_x", "tableId": "t1", **extra}


# ── payload_error (gate del PUT /changes, bulk y del apply) ─────────────────

def test_payload_error_acepta_custom_sql_valido():
    p = _view_payload(customSql="WITH b AS (SELECT id FROM t) SELECT id FROM b")
    assert validation.payload_error("views", "v1", "upsert", p) is None


def test_payload_error_rechaza_custom_sql_invalido():
    p = _view_payload(customSql="SELECT * FROM t")
    err = validation.payload_error("views", "v1", "upsert", p)
    assert err is not None and "custom SQL" in err


def test_payload_error_ignora_custom_sql_vacio_y_deletes():
    assert validation.payload_error("views", "v1", "upsert", _view_payload()) is None
    assert validation.payload_error("views", "v1", "delete", None) is None


def test_payload_error_no_afecta_otras_colecciones():
    assert validation.payload_error(
        "canonical_tables", "t1", "upsert",
        {"id": "t1", "projectId": "p1", "physicalName": "M_X", "logicalName": "X"}) is None


# ── apply_plan re-deriva customColumns ──────────────────────────────────────

def test_apply_plan_rederiva_custom_columns():
    changes = {"views": {"v1": {"op": "upsert", "payload": _view_payload(
        customSql="SELECT id, UPPER(n) AS n_up FROM t1",
        customColumns=[{"name": "INVENTADA"}])}}}
    plan = apply_plan(changes)
    payload = plan[0][3]
    assert payload["customColumns"] == [
        {"name": "id"}, {"name": "n_up", "expression": "UPPER(n)"}]


def test_apply_plan_limpia_custom_columns_sin_custom_sql():
    changes = {"views": {"v1": {"op": "upsert", "payload": _view_payload(
        customSql="  ", customColumns=[{"name": "zombi"}])}}}
    plan = apply_plan(changes)
    payload = plan[0][3]
    assert payload["customSql"] is None
    assert payload["customColumns"] == []


def test_apply_plan_sigue_normalizando_fuentes():
    changes = {"views": {"v1": {"op": "upsert", "payload": {
        "id": "v1", "name": "v", "tableId": "t9"}}}}
    plan = apply_plan(changes)
    assert plan[0][3]["sourceTableIds"] == ["t9"]
