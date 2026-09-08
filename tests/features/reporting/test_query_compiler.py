"""Compiler del motor de reporting (PURO): traducción QuerySpec → pipeline Mongo,
whitelist de campos/ops, pushdown y planner de escala."""
from __future__ import annotations

import pytest

from app.features.reporting.query.compiler import QueryError, build_match, compile_spec
from app.features.reporting.query.schema import build_catalog
from app.features.reporting.query.spec import QuerySpec

UDP = [{"id": "u1", "name": "Clasificación", "level": "column", "dataType": "list",
        "allowedValues": ["DAC", "NO DAC"]}]
CAT = build_catalog("columns", UDP)


def _spec(**kw):
    return QuerySpec.model_validate({"from": "columns", "projectId": "p1", **kw})


def test_where_traduce_a_match_con_ops():
    spec = _spec(where={"op": "and", "conditions": [
        {"field": "dataType", "op": "eq", "value": "BIGINT"},
        {"field": "isPrimaryKey", "op": "eq", "value": True},
        {"field": "physicalName", "op": "startsWith", "value": "ID"}]})
    m = build_match(spec.where, CAT)
    assert m["$and"][0] == {"dataType": "BIGINT"}
    assert m["$and"][1] == {"isPrimaryKey": True}
    assert m["$and"][2]["physicalName"]["$regex"] == "^ID"   # re.escape del literal


def test_udp_filtra_por_path_embebido():
    spec = _spec(where={"op": "and", "conditions": [{"field": "udp.u1", "op": "in", "value": ["DAC", "NO DAC"]}]})
    m = build_match(spec.where, CAT)
    assert m == {"udpValues.u1": {"$in": ["DAC", "NO DAC"]}}   # key pública → path embebido


def test_campo_desconocido_es_400():
    spec = _spec(where={"op": "and", "conditions": [{"field": "hackerField", "op": "eq", "value": "x"}]})
    with pytest.raises(QueryError) as e:
        build_match(spec.where, CAT)
    assert e.value.code == 400


def test_op_no_permitida_por_tipo_es_422():
    # 'contains' no aplica a boolean
    spec = _spec(where={"op": "and", "conditions": [{"field": "isPrimaryKey", "op": "contains", "value": "x"}]})
    with pytest.raises(QueryError) as e:
        build_match(spec.where, CAT)
    assert e.value.code == 422


def test_planner_rechaza_orden_sin_indice():
    # logicalName no es sortable (sin índice) → 422 a escala
    with pytest.raises(QueryError) as e:
        compile_spec(_spec(orderBy=[{"field": "logicalName", "dir": "asc"}]), CAT)
    assert e.value.code == 422


def test_orden_por_campo_indexado_ok():
    c = compile_spec(_spec(orderBy=[{"field": "physicalName", "dir": "desc"}]), CAT)
    assert c.sort == [("physicalName", -1)] and not c.grouped


def test_group_by_con_count():
    c = compile_spec(_spec(groupBy=["dataType"], aggregations=[{"fn": "count", "as": "n"}]), CAT)
    assert c.grouped
    assert c.group["_id"]["dataType"] == {"$ifNull": ["$dataType", None]}
    assert c.group["n"] == {"$sum": 1}
