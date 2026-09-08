"""Query de listado de vistas (F3a): matchea `sourceTableIds` (en Mongo la
igualdad sobre un array es *contains* y `$in` es intersección) con fallback
OR a `tableId` legacy para docs aún no migrados. Pura → sin DB."""
from __future__ import annotations

from app.features.views.repository import build_query


def test_query_sin_filtros_solo_activas():
    assert build_query() == {"flgactive": {"$ne": False}}


def test_query_por_tableid_matchea_sources_y_legacy():
    q = build_query(table_id="t1")
    assert q["flgactive"] == {"$ne": False}
    assert q["$or"] == [{"sourceTableIds": "t1"}, {"tableId": "t1"}]


def test_query_por_tableids_batch():
    q = build_query(table_ids=["t1", "t2"])
    assert q["$or"] == [{"sourceTableIds": {"$in": ["t1", "t2"]}},
                        {"tableId": {"$in": ["t1", "t2"]}}]


def test_tableid_tiene_precedencia_sobre_tableids():
    # Contrato actual del router: `tableId` puntual gana al CSV `tableIds`.
    q = build_query(table_id="t1", table_ids=["t2"])
    assert q["$or"] == [{"sourceTableIds": "t1"}, {"tableId": "t1"}]


def test_build_query_con_proyecto_acota():
    """Doc 75 D6: listar sin tabla exige proyecto; el filtro lo lleva."""
    assert build_query(project_id="p1") == {"flgactive": {"$ne": False}, "projectId": "p1"}
    assert build_query(table_id="t1", project_id="p1")["projectId"] == "p1"
