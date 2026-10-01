"""Doc 105 (cierre del coordinador) — `GET /api/reporting/columns` con tope en
modo versión.

Producción acota la lectura (`limit` o `UNFILTERED_COLUMNS_CAP = 20000` sin
filtro de tabla) y trunca EN SILENCIO: la respuesta es la lista de filas, sin
flag ni 422 (`repository.columns` → `cur.limit(limit)`). En modo versión el
tope se aplicaba sólo a lo publicado y la superposición sumaba TODAS las altas
del draft: por API, sobre un draft enorme, la respuesta no tenía límite. Ahora
el mismo tope, con la misma forma, después de superponer."""
from __future__ import annotations

import pytest

from app.features.reporting import service

from .helpers import P, ch

ANA = {"X-Dev-User": "ana"}
# La versión completa (sin tope) de `report_version_db` + 10 altas del draft:
# c1, c3 (c2 baja) + c4 (alta), c9 (revive) + cn0..cn9.
FULL = {"c1", "c3", "c4", "c9", *(f"cn{i}" for i in range(10))}


@pytest.fixture
def big_draft(report_version_db):
    report_version_db.raw["changeset_changes"].insert_many([
        ch("canonical_columns", f"cn{i}", "upsert",
           {"tableId": f"t{1 + i % 3}", "physicalName": f"NUEVA_{i}", "logicalName": f"Nueva {i}",
            "dataType": "INT", "ordinal": 10 + i})
        for i in range(10)])
    return report_version_db


def _columns(client, **params) -> list[dict]:
    res = client.get("/api/reporting/columns", params={"projectId": P, **params}, headers=ANA)
    assert res.status_code == 200, res.text[:200]
    data = res.json()["data"]
    assert isinstance(data, list)                                  # la forma de producción: lista, sin flag
    return data


def _sorted_like_production(rows: list[dict]) -> bool:
    keys = [(r.get("tableId") or "", r.get("ordinal") or 0) for r in rows]
    return keys == sorted(keys)


def test_sin_tope_holgado_trae_la_version_completa(big_draft, client):
    assert {r["id"] for r in _columns(client, changesetId="cs1", limit=100)} == FULL


def test_limit_explicito_acota_la_version_como_a_produccion(big_draft, client):
    rows = _columns(client, changesetId="cs1", limit=5)
    assert len(rows) == 5 and {r["id"] for r in rows} <= FULL and _sorted_like_production(rows)
    assert len(_columns(client, limit=2)) == 2                    # producción: igual que antes


def test_el_tope_por_defecto_acota_la_version(big_draft, client, monkeypatch):
    monkeypatch.setattr(service, "UNFILTERED_COLUMNS_CAP", 4)
    rows = _columns(client, changesetId="cs1")
    assert len(rows) == 4 and {r["id"] for r in rows} <= FULL and _sorted_like_production(rows)


def test_con_tabla_y_limit_tambien_acota(big_draft, client):
    """`tableId` + `limit`: lo publicado ya venía acotado y las altas de esa
    tabla se sumaban encima."""
    rows = _columns(client, changesetId="cs1", tableId="t1", limit=2)
    assert len(rows) == 2 and {r["tableId"] for r in rows} == {"t1"}


def test_los_lotes_no_tienen_tope(big_draft, client):
    """El POST de lote no lleva `limit`: el lote ya acota (sin cambios)."""
    res = client.post("/api/reporting/columns/query",
                      json={"projectId": P, "tableIds": ["t1", "t2", "t3", "t4"], "changesetId": "cs1"}, headers=ANA)
    assert res.status_code == 200 and {r["id"] for r in res.json()["data"]} == FULL
