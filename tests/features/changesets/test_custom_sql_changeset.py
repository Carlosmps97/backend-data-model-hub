"""Doc 91 D6: el camino CHANGESET ya NO valida el User-Defined SQL (se guarda lo
que el modelador escribió) y `apply_plan` sólo lo normaliza (strip; vacío ⇒
Regular). Sin `customColumns`."""
from __future__ import annotations

from app.features.changesets import validation
from app.features.changesets.service import apply_plan


def _view_payload(**extra) -> dict:
    return {"id": "v1", "projectId": "p1", "name": "v_x", "tableId": "t1", **extra}


# ── payload_error (gate del PUT /changes, bulk y del apply) ─────────────────

def test_payload_error_acepta_custom_sql_valido():
    p = _view_payload(customSql="WITH b AS (SELECT id FROM t) SELECT id FROM b")
    assert validation.payload_error("views", "v1", "upsert", p) is None


def test_payload_error_acepta_custom_sql_que_no_parsea():
    # Doc 91 D6: sintaxis inválida, SELECT * o varios statements ya no bloquean.
    for sql in ("SELEC x FRM t", "SELECT * FROM t", "SELECT 1; SELECT 2"):
        assert validation.payload_error("views", "v1", "upsert", _view_payload(customSql=sql)) is None


def test_payload_error_ignora_custom_sql_vacio_y_deletes():
    assert validation.payload_error("views", "v1", "upsert", _view_payload()) is None
    assert validation.payload_error("views", "v1", "delete", None) is None


def test_payload_error_no_afecta_otras_colecciones():
    assert validation.payload_error(
        "canonical_tables", "t1", "upsert",
        {"id": "t1", "projectId": "p1", "physicalName": "M_X", "logicalName": "X"}) is None


# ── apply_plan normaliza customSql ──────────────────────────────────────────

def test_apply_plan_publica_custom_sql_verbatim_con_strip():
    changes = {"views": {"v1": {"op": "upsert", "payload": _view_payload(
        customSql="  CREATE VIEW s.v_x AS SELEC id FRM t1 \n")}}}
    payload = apply_plan(changes)[0][3]
    assert payload["customSql"] == "CREATE VIEW s.v_x AS SELEC id FRM t1"
    assert "customColumns" not in payload


def test_apply_plan_custom_sql_vacio_es_regular():
    changes = {"views": {"v1": {"op": "upsert", "payload": _view_payload(customSql="  ")}}}
    assert apply_plan(changes)[0][3]["customSql"] is None


def test_apply_plan_sigue_normalizando_fuentes():
    changes = {"views": {"v1": {"op": "upsert", "payload": {
        "id": "v1", "name": "v", "tableId": "t9"}}}}
    plan = apply_plan(changes)
    assert plan[0][3]["sourceTableIds"] == ["t9"]
