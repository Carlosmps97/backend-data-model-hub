"""Impacto de eliminar una columna (spec 10 §8) — `impact_rows` pura."""
from __future__ import annotations

from app.features.relationships.service import impact_rows

REL = {"id": "r1", "projectId": "p1", "parentTableId": "tA", "childTableId": "tB",
       "pairs": [{"parentColumnId": "cA", "childColumnId": "cB", "roleName": None}],
       "parentCardinality": "one", "childCardinality": "many", "identifying": False}
CANVASES = [
    {"id": "sa1", "name": "Ventas", "tableIds": ["tA", "tB"]},
    {"id": "sa2", "name": "Riesgos", "tableIds": ["tA"]},  # NO tiene la otra tabla
]


def test_lado_parent_apunta_al_extremo_child():
    rows = impact_rows("cA", [REL], {"tB": "CUENTA"}, {"cB": "ID_CTA"}, CANVASES)
    assert rows == [{
        "relId": "r1", "otherTableId": "tB", "otherTableName": "CUENTA",
        "otherColumnId": "cB", "otherColumnName": "ID_CTA", "thisSide": "parent",
        "parentCardinality": "one", "childCardinality": "many", "identifying": False,
        "canvases": [{"id": "sa1", "name": "Ventas"}],  # sa2 no contiene AMBAS tablas
    }]


def test_lado_child_apunta_al_extremo_parent():
    rows = impact_rows("cB", [REL], {"tA": "CLIENTE"}, {"cA": "ID_CLI"}, [])
    assert rows[0]["thisSide"] == "child"
    assert rows[0]["otherTableName"] == "CLIENTE"
    assert rows[0]["otherColumnName"] == "ID_CLI"
    assert rows[0]["canvases"] == []


def test_nombre_desconocido_cae_al_id():
    rows = impact_rows("cA", [REL], {}, {}, [])
    assert rows[0]["otherTableName"] == "tB"
    assert rows[0]["otherColumnName"] == "cB"


def test_par_compuesto_junta_columnas_del_otro_extremo():
    rel = {**REL, "pairs": [
        {"parentColumnId": "cA", "childColumnId": "cB"},
        {"parentColumnId": "cA", "childColumnId": "cB2"},
    ]}
    rows = impact_rows("cA", [rel], {}, {"cB": "ID_CTA", "cB2": "ID_MON"}, [])
    assert len(rows) == 1  # una fila POR RELACIÓN, no por par
    assert rows[0]["otherColumnName"] == "ID_CTA, ID_MON"


# ── column_impact (async): publicado + overlay del changeset ───────────────
import asyncio
from unittest.mock import AsyncMock

from app.features.relationships import service


def test_column_impact_sin_relaciones(monkeypatch):
    monkeypatch.setattr(service.repository, "list_for_column", AsyncMock(return_value=[]))
    out = asyncio.run(service.column_impact("cX", None))
    assert out == {"relationships": [], "total": 0}


def test_column_impact_sin_changeset_publicado(monkeypatch):
    monkeypatch.setattr(service.repository, "list_for_column", AsyncMock(return_value=[REL]))
    monkeypatch.setattr(service.repository, "canvases_containing",
                        AsyncMock(return_value=[{"id": "sa1", "name": "Ventas", "tableIds": ["tA", "tB"]}]))

    async def _pub(collection, flt=None, **kwargs):
        return {"canonical_tables": [{"id": "tB", "physicalName": "CUENTA"}],
                "canonical_columns": [{"id": "cB", "physicalName": "ID_CTA"}]}[collection]

    monkeypatch.setattr(service.cs_repo, "published", _pub)
    out = asyncio.run(service.column_impact("cA", None))
    assert out["total"] == 1
    row = out["relationships"][0]
    assert row["otherTableName"] == "CUENTA"
    assert row["otherColumnName"] == "ID_CTA"
    assert row["canvases"] == [{"id": "sa1", "name": "Ventas"}]


def test_column_impact_overlay_del_changeset(monkeypatch):
    """El changeset BORRA r1 y AGREGA r2 hacia una tabla nueva (tC, aún no
    publicada): el impact refleja el estado efectivo y toma el nombre del
    payload del changeset."""
    monkeypatch.setattr(service.repository, "list_for_column", AsyncMock(return_value=[REL]))
    monkeypatch.setattr(service.repository, "canvases_containing",
                        AsyncMock(return_value=[{"id": "sa1", "name": "Ventas", "tableIds": ["tA", "tB"]}]))
    monkeypatch.setattr(service.cs_repo, "changes_map", AsyncMock(return_value={
        "relationships": {
            "r1": {"op": "delete"},
            "r2": {"op": "upsert", "payload": {"projectId": "p1", 
                "sourceTableId": "tA", "sourceColumnId": "cA",
                "targetTableId": "tC", "targetColumnId": "cC",
                "sourceCardinality": "one", "targetCardinality": "many"}},
        },
        "canonical_tables": {"tC": {"op": "upsert", "payload": {"projectId": "p1", 
            "physicalName": "PRODUCTO", "logicalName": "producto"}}},
    }))
    monkeypatch.setattr(service.cs_repo, "published", AsyncMock(return_value=[]))
    out = asyncio.run(service.column_impact("cA", "cs1"))
    assert out["total"] == 1
    row = out["relationships"][0]
    assert row["relId"] == "r2"
    assert row["otherTableName"] == "PRODUCTO"   # del payload (no publicada)
    assert row["otherColumnName"] == "cC"        # sin nombre conocido → id
    assert row["canvases"] == []                 # sa1 no contiene tC
