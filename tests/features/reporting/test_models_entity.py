"""F5 — entidad `models` (→ subject_areas) en el Field Catalog: campos base +
UDP dinámicos level='canvas', y reglas del compiler para el campo calculado
`tableCount` (derived: select sí; where/groupBy/agg → 422)."""
from __future__ import annotations

import pytest

from app.features.reporting.query.compiler import QueryError, build_match, compile_spec
from app.features.reporting.query.executor import COLL_OF, DEFAULT_SORT_FIELD, _row
from app.features.reporting.query.schema import build_catalog
from app.features.reporting.query.spec import QuerySpec

UDP = [
    {"id": "u9", "name": "Dominio funcional", "level": "canvas", "dataType": "list",
     "allowedValues": ["Riesgos", "Finanzas"]},
    {"id": "u1", "name": "Clasificación", "level": "column", "dataType": "string"},
]
CAT = build_catalog("models", UDP)


def _spec(**kw):
    return QuerySpec.model_validate({"from": "models", "projectId": "p1", **kw})


def test_models_mapea_a_subject_areas_y_ordena_por_name():
    assert COLL_OF["models"] == "subject_areas"
    assert DEFAULT_SORT_FIELD["models"] == "name"


def test_catalog_models_campos_base_y_udp_canvas():
    assert {"name", "folderId", "tableCount", "udp.u9"} <= set(CAT)
    assert "projectId" not in CAT                   # doc 75: la consulta ya es de un proyecto
    assert "udp.u1" not in CAT                       # level='column' NO aparece acá
    assert CAT["udp.u9"].path == "udpValues.u9"      # cubierto por el wildcard
    assert CAT["udp.u9"].enumValues == ["Riesgos", "Finanzas"]
    assert CAT["tableCount"].hydrate == "derived"


def test_from_models_es_valido_en_el_spec():
    assert _spec().from_ == "models"


def test_filtro_por_udp_canvas_compila_al_path_embebido():
    spec = _spec(where={"op": "and", "conditions": [
        {"field": "udp.u9", "op": "eq", "value": "Riesgos"}]})
    assert build_match(spec.where, CAT) == {"udpValues.u9": "Riesgos"}


def test_orden_por_name_ok():
    c = compile_spec(_spec(orderBy=[{"field": "name", "dir": "asc"}]), CAT)
    assert c.sort == [("name", 1)] and not c.grouped


def test_table_count_no_filtrable():
    spec = _spec(where={"op": "and", "conditions": [
        {"field": "tableCount", "op": "gt", "value": 5}]})
    with pytest.raises(QueryError) as e:
        build_match(spec.where, CAT)
    assert e.value.code == 422


def test_table_count_no_agrupable_ni_agregable():
    with pytest.raises(QueryError):
        compile_spec(_spec(groupBy=["tableCount"],
                           aggregations=[{"fn": "count", "as": "n"}]), CAT)
    with pytest.raises(QueryError):
        compile_spec(_spec(groupBy=["projectId"],
                           aggregations=[{"fn": "sum", "field": "tableCount", "as": "s"}]), CAT)


def test_row_calcula_table_count_post_fetch():
    c = compile_spec(_spec(select=["name", "tableCount"]), CAT)
    row = _row({"_id": "sa1", "name": "Banking", "tableIds": ["t1", "t2", "t3"]}, c.select, {})
    assert row["tableCount"] == 3
    assert row["name"] == "Banking"


def test_catalog_publico_campo_derived_no_ofrece_ops_ni_facets():
    # tableCount (hydrate='derived') se publica con ops=[] + derived=True: el
    # builder NO lo ofrece como filtro (F5 T7 review). name mantiene sus ops.
    pub = {f["key"]: f for f in (fd.to_public() for fd in CAT.values())}
    assert pub["tableCount"]["ops"] == []
    assert pub["tableCount"]["derived"] is True
    assert pub["name"]["ops"] == list(CAT["name"].ops) and pub["name"]["ops"]
    assert pub["name"]["derived"] is False
