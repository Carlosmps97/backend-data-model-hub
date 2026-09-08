"""Doc 75 D19 — `projectId` se resuelve contra la columna generada `project_id`
en modo table (igualdad y $in); todo lo demás sigue por jsonb. Puro: el SQL se
compara como texto, sin base."""
from __future__ import annotations

from app.core.db.lakebase.aggregate import compile_pipeline
from app.core.db.lakebase.collection import LakebaseDatabase
from app.core.db.lakebase.translate import Sql, filter_sql


def test_igualdad_e_in_usan_la_columna():
    s = Sql()
    assert filter_sql({"projectId": "p1"}, s) == "project_id = $1"
    assert s.params == ["p1"]
    s = Sql()
    assert filter_sql({"projectId": {"$eq": "p1"}}, s) == "(project_id = $1)"
    s = Sql()
    assert filter_sql({"projectId": {"$in": ["a", "b"]}}, s) == "(project_id = ANY($1::text[]))"
    assert s.params == [["a", "b"]]


def test_combinado_con_otros_campos_conserva_el_orden_de_parametros():
    s = Sql()
    sql = filter_sql({"projectId": "p1", "flgactive": {"$ne": False}}, s)
    assert sql.startswith("project_id = $1 AND ")
    assert s.params[0] == "p1"


def test_ne_none_exists_y_modo_doc_siguen_por_jsonb():
    for flt in ({"projectId": {"$ne": "p1"}}, {"projectId": None}, {"projectId": {"$exists": True}},
                {"projectId": {"$in": ["a", None]}}):
        assert "project_id" not in filter_sql(flt, Sql()), flt
    assert "project_id" not in filter_sql({"projectId": "p1"}, Sql(), doc="d", mode="doc")


def test_pushdown_del_primer_match_en_aggregate():
    s = Sql()
    sql = compile_pipeline([{"$match": {"projectId": "p1"}}, {"$count": "n"}], '"s"."t"', s)
    assert sql.startswith('SELECT jsonb_build_object($2::text, count(*)) AS d '
                          'FROM (SELECT doc AS d FROM "s"."t" WHERE project_id = $1) q1')
    assert s.params == ["p1", "n"]
    # Un $match que NO es la primera stage sigue en modo doc (sobre `d`).
    s = Sql()
    assert "project_id" not in compile_pipeline([{"$unwind": "$xs"}, {"$match": {"projectId": "p1"}}], '"s"."t"', s)


def test_index_ddl_usa_la_columna_para_project_id():
    name = LakebaseDatabase._index_name("canonical_tables", [("projectId", 1), ("physicalName", 1)])
    assert name == "ix_canonical_tables_project_id_physicalName"
    ddl = LakebaseDatabase._index_ddl("canonical_tables", [("projectId", 1), ("physicalName", 1)], unique=False, schema="s")
    assert ddl == ('CREATE INDEX IF NOT EXISTS "ix_canonical_tables_project_id_physicalName" '
                   'ON "s"."canonical_tables" (project_id ASC, ((doc ->> \'physicalName\') COLLATE "C") ASC)')
    ddl = LakebaseDatabase._index_ddl("standards_versions", [("projectId", 1), ("seq", 1)], unique=True, schema="s")
    assert ddl.startswith('CREATE UNIQUE INDEX IF NOT EXISTS "ix_standards_versions_project_id_seq" ')
    # Un campo cualquiera sigue siendo expresión jsonb (sin cambio).
    assert LakebaseDatabase._index_ddl("views", [("tableId", 1)], unique=False, schema="s") == (
        'CREATE INDEX IF NOT EXISTS "ix_views_tableId" ON "s"."views" (((doc ->> \'tableId\') COLLATE "C") ASC)')
