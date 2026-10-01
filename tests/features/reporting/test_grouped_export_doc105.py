"""Doc 105 (cierre del coordinador) — el export CSV de un AGRUPADO entrega todos
los grupos (o hasta `maxRows` si el SQL trae LIMIT).

Antes pedía una «página» de 2 000 grupos (el agrupado no pagina) y cortaba el
CSV EN SILENCIO: el CSV no tiene por dónde avisar. Ahora el export pide todos
los grupos en UNA agregación, antes de abrir el stream, hasta un tope alto y
fijo (`MAX_EXPORT_GROUPS`); si el resultado lo pasaría, responde un 422 claro —
nunca un CSV cortado. `/query` no cambia (su página + el aviso)."""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from app.core.db import client as db_client
from app.features.reporting.query import executor as ex
from app.main import app

from .lakebase_sql_doc105 import LakebaseCheckingDb

TOO_MANY = "The grouped result has more than 5 groups: filter, group by fewer fields or add LIMIT."


def _db(monkeypatch, n: int) -> LakebaseCheckingDb:
    fake = LakebaseCheckingDb()
    monkeypatch.setattr(db_client, "_pg_db", fake)
    fake.raw["canonical_columns"].insert_many([
        {"_id": f"c{i:05d}", "projectId": "p1", "flgactive": True, "tableId": "t1", "physicalName": f"C{i:05d}",
         "dataType": "INT"} for i in range(n)])
    return fake


@pytest.fixture
def raw() -> TestClient:
    return TestClient(app, raise_server_exceptions=False)


def _grouped(**extra) -> dict:
    return {"projectId": "p1", "from": "columns", "groupBy": ["physicalName"],
            "aggregations": [{"fn": "count", "as": "n"}], **extra}


def _sql_spec(raw, limit: int) -> dict:
    res = raw.post("/api/reporting/query/validate", json={
        "text": f"SELECT physicalName, COUNT(*) AS n FROM columns GROUP BY physicalName LIMIT {limit}",
        "projectId": "p1"})
    return res.json()["data"]["spec"]


def test_export_agrupado_trae_todos_los_grupos(monkeypatch, raw):
    """2 050 grupos: antes salían 2 000 (la «página» del export) sin aviso."""
    db = _db(monkeypatch, 2050)
    res = raw.post("/api/reporting/export", json=_grouped())
    assert res.status_code == 200 and res.headers["content-type"].startswith("text/csv")
    lines = res.text.splitlines()
    assert len(lines) == 1 + 2050 and lines[-1] == "C02049,1"
    assert not db.problems, db.problems


def test_export_agrupado_sobre_el_tope_es_422_antes_del_stream(monkeypatch, raw):
    _db(monkeypatch, 12)
    monkeypatch.setattr(ex, "MAX_EXPORT_GROUPS", 5, raising=False)
    res = raw.post("/api/reporting/export", json=_grouped())
    assert res.status_code == 422 and res.json()["detail"] == TOO_MANY
    assert not res.headers["content-type"].startswith("text/csv")


@pytest.mark.parametrize("limit, lines", [(3, 3), (5, 5)])
def test_export_agrupado_con_limit_dentro_del_tope(monkeypatch, raw, limit, lines):
    """El LIMIT pedido corta (es el resultado), sin error."""
    _db(monkeypatch, 12)
    monkeypatch.setattr(ex, "MAX_EXPORT_GROUPS", 5, raising=False)
    res = raw.post("/api/reporting/export", json=_sql_spec(raw, limit))
    assert res.status_code == 200 and len(res.text.splitlines()) == 1 + lines


def test_export_agrupado_con_limit_sobre_el_tope(monkeypatch, raw):
    """LIMIT 8 con tope 5: hay 12 grupos → se pasaría del tope → 422 (antes: 8)."""
    _db(monkeypatch, 12)
    monkeypatch.setattr(ex, "MAX_EXPORT_GROUPS", 5, raising=False)
    res = raw.post("/api/reporting/export", json=_sql_spec(raw, 8))
    assert res.status_code == 422 and res.json()["detail"] == TOO_MANY


def test_export_agrupado_con_limit_sobre_el_tope_pero_pocos_grupos(monkeypatch, raw):
    _db(monkeypatch, 4)
    monkeypatch.setattr(ex, "MAX_EXPORT_GROUPS", 5, raising=False)
    res = raw.post("/api/reporting/export", json=_sql_spec(raw, 8))
    assert res.status_code == 200 and len(res.text.splitlines()) == 1 + 4


def test_query_agrupado_no_cambia(monkeypatch, raw):
    """`/query` sigue con su página de grupos y el aviso (no con el tope del export)."""
    _db(monkeypatch, 12)
    monkeypatch.setattr(ex, "MAX_EXPORT_GROUPS", 5, raising=False)
    res = raw.post("/api/reporting/query", json=_grouped(limit=8))
    data = res.json()["data"]
    assert res.status_code == 200 and len(data["rows"]) == 8
    assert data["meta"]["warnings"] == ["Only the first 8 groups are shown: filter or group by fewer values."]
