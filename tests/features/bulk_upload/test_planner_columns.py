"""Planner — columnas (doc 55 §3.3-3.4, §4.2, §5): referencia a la tabla,
identidad, tipo/dominio, PK, partición, ordinal, UDP, duplicados, unchanged
y warnings. Puro."""
from __future__ import annotations

from app.features.bulk_upload.planner import build_plan

from .helpers import by_coll, codes, crow, ctx, naming, parsed, seq_ids, trow

_DOMAINS = [{"id": "d1", "name": "Codigo", "defaultDataType": "STRING"},
            {"id": "d2", "name": "Monto", "defaultDataType": "DECIMAL(18,2)"},
            {"id": "d3", "name": "Numero Oracle", "defaultDataType": "NUMBER(22,3)"}]
_SCHEMAS = [{"id": "s1", "name": "ddv", "kind": "tables"}]


def _table(tid="t1", physical="CLIENTE", logical="Cliente"):
    return {"id": tid, "physicalName": physical, "logicalName": logical, "schema": "ddv",
            "description": None, "udpValues": {}}


def _col(cid, physical, logical, ordinal, pk=False, pk_pos=None, **kw):
    doc = {"id": cid, "projectId": "p1", "tableId": "t1", "physicalName": physical, "logicalName": logical,
           "parentDomainId": None, "dataType": "STRING", "typeOverridden": False,
           "isPrimaryKey": True if pk else None, "pkPosition": pk_pos, "isForeignKey": None,
           "isNullable": not pk, "isPartition": False, "description": None, "ordinal": ordinal, "udpValues": {}}
    doc.update(kw)
    return doc


def _cols(plan):
    return by_coll(plan).get("canonical_columns", [])


def test_columnas_de_tabla_nueva_ordinal_desde_cero_y_tipo_heredado():
    p = parsed(tables=[trow(3, "Cliente", schema="ddv")],
               columns=[crow(3, "cliente", "Codigo Cliente", domain="codigo"),
                        crow(4, "Cliente", "Nombre", data_type="varchar(50)")])
    plan = build_plan(p, ctx(schemas=_SCHEMAS, domains=_DOMAINS), new_id=seq_ids())
    assert plan.has_errors is False
    tid = by_coll(plan)["canonical_tables"][0]["entityId"]
    c1, c2 = (c["payload"] for c in _cols(plan))
    assert (c1["tableId"], c1["physicalName"], c1["logicalName"], c1["ordinal"]) == (tid, "CODIGOCLIENTE", "Codigo Cliente", 0)
    assert (c1["parentDomainId"], c1["dataType"], c1["typeOverridden"]) == ("d1", "STRING", False)
    assert (c2["dataType"], c2["typeOverridden"], c2["ordinal"], c2["isNullable"], c2["isPrimaryKey"]) == ("VARCHAR(50)", False, 1, True, None)
    assert plan.report["summary"]["columns"] == {"create": 2, "update": 0, "unchanged": 0}


def test_columna_nueva_sin_tipo_ni_dominio_es_error():
    p = parsed(tables=[trow(3, "Cliente", schema="ddv")], columns=[crow(3, "Cliente", "Nombre")])
    plan = build_plan(p, ctx(schemas=_SCHEMAS))
    (e,) = plan.report["errors"]
    assert (e["sheet"], e["code"], e["column"], e["row"]) == ("Atributos", "missing-required", "TIPO_DATO", 3)
    assert _cols(plan) == []


def test_tipo_invalido_y_dominio_desconocido_son_errores():
    p = parsed(tables=[trow(3, "Cliente", schema="ddv")],
               columns=[crow(3, "Cliente", "A", data_type="texto raro"), crow(4, "Cliente", "B", domain="Inexistente")])
    plan = build_plan(p, ctx(schemas=_SCHEMAS, domains=_DOMAINS))
    assert sorted((e["code"], e["row"]) for e in plan.report["errors"]) == [("invalid-type", 3), ("missing-required", 4), ("unknown-domain", 4)]


def test_tipo_distinto_al_default_del_dominio_es_override_y_el_igual_no():
    p = parsed(tables=[trow(3, "Cliente", schema="ddv")],
               columns=[crow(3, "Cliente", "A", domain="Codigo", data_type="bigint"),
                        crow(4, "Cliente", "B", domain="Codigo", data_type="string")])
    plan = build_plan(p, ctx(schemas=_SCHEMAS, domains=_DOMAINS))
    a, b = (c["payload"] for c in _cols(plan))
    assert (a["dataType"], a["typeOverridden"]) == ("BIGINT", True)
    assert (b["dataType"], b["typeOverridden"]) == ("STRING", False)


def test_tipo_extra_del_dominio_se_acepta_con_su_grafia():
    p = parsed(tables=[trow(3, "Cliente", schema="ddv")],
               columns=[crow(3, "Cliente", "A", domain="Numero Oracle", data_type="number (22, 3)")])
    plan = build_plan(p, ctx(schemas=_SCHEMAS, domains=_DOMAINS))
    (a,) = (c["payload"] for c in _cols(plan))
    assert (a["dataType"], a["typeOverridden"]) == ("NUMBER(22,3)", False)


def test_pk_posiciones_por_orden_de_la_hoja_y_no_nulable():
    p = parsed(tables=[trow(3, "Cliente", schema="ddv")],
               columns=[crow(3, "Cliente", "A", data_type="STRING", pk=True),
                        crow(4, "Cliente", "B", data_type="STRING"),
                        crow(5, "Cliente", "C", data_type="STRING", pk=True)])
    plan = build_plan(p, ctx(schemas=_SCHEMAS))
    a, b, c = (x["payload"] for x in _cols(plan))
    assert (a["isPrimaryKey"], a["pkPosition"], a["isNullable"]) == (True, 0, False)
    assert (b["isPrimaryKey"], b["pkPosition"], b["isNullable"]) == (None, None, True)
    assert (c["isPrimaryKey"], c["pkPosition"], c["isNullable"]) == (True, 1, False)


def test_solo_atributos_agrega_columnas_a_tabla_existente_por_logico():
    c = ctx(schemas=_SCHEMAS, tables=[_table()], columns_by_table={"t1": [_col("c1", "COD", "Codigo", 0, pk=True, pk_pos=0)]})
    plan = build_plan(parsed(columns=[crow(3, "cliente", "Nombre", data_type="STRING")]), c, new_id=seq_ids())
    assert plan.has_errors is False
    (col,) = _cols(plan)
    assert (col["payload"]["tableId"], col["payload"]["ordinal"], col["entityId"]) == ("t1", 1, "n1")
    assert "canonical_tables" not in by_coll(plan)
    assert plan.report["summary"]["tables"] == {"create": 0, "update": 0, "unchanged": 0}
    assert plan.report["summary"]["columns"] == {"create": 1, "update": 0, "unchanged": 0}
    (row,) = plan.report["tables"]
    assert (row["row"], row["physicalName"], row["action"], row["columns"]) == (None, "CLIENTE", "unchanged", {"create": 1, "update": 0, "unchanged": 0})


def test_tabla_desconocida_y_ambigua_son_errores():
    c = ctx(tables=[_table("t1", "A1", "Dup"), _table("t2", "A2", "Dup")])
    plan = build_plan(parsed(columns=[crow(3, "Nadie", "X", data_type="STRING"), crow(4, "Dup", "Y", data_type="STRING")]), c)
    assert sorted((e["code"], e["row"]) for e in plan.report["errors"]) == [("ambiguous-table", 4), ("unknown-table", 3)]


def test_columna_existente_por_fisico_actualiza_con_doc_completo():
    c = ctx(schemas=_SCHEMAS, tables=[_table()],
            columns_by_table={"t1": [_col("c1", "COD", "Codigo", 0, isForeignKey=True, udpValues={"u9": "keep"})]})
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Codigo", physical="cod", description="d")]), c)
    (col,) = _cols(plan)
    assert col["entityId"] == "c1"
    assert col["payload"]["description"] == "d" and col["payload"]["ordinal"] == 0
    assert col["payload"]["isForeignKey"] is True and col["payload"]["udpValues"] == {"u9": "keep"}
    assert col["payload"]["dataType"] == "STRING"          # tipo vacío en update = conserva
    assert codes(plan, "warning") == ["rename", "existing-column"]   # grafía del físico cambia


def test_columna_existente_sin_cambios_queda_unchanged():
    c = ctx(schemas=_SCHEMAS, tables=[_table()], columns_by_table={"t1": [_col("c1", "COD", "Codigo", 0)]})
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Codigo", physical="COD")]), c)
    assert plan.changes == [] and plan.report["warnings"] == []
    assert plan.report["summary"]["columns"] == {"create": 0, "update": 0, "unchanged": 1}


def test_fallback_por_logico_en_columnas_conserva_fisico():
    c = ctx(schemas=_SCHEMAS, tables=[_table()], columns_by_table={"t1": [_col("c1", "CODCLI", "Codigo Cliente", 0)]})
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Codigo Cliente", description="d")]), c)
    (col,) = _cols(plan)
    assert col["entityId"] == "c1" and col["payload"]["physicalName"] == "CODCLI"
    assert codes(plan, "warning") == ["matched-by-logical", "existing-column"]


def test_pk_existente_no_listada_conserva_su_posicion_y_las_nuevas_van_despues():
    c = ctx(schemas=_SCHEMAS, tables=[_table()], columns_by_table={"t1": [_col("c1", "COD", "Codigo", 0, pk=True, pk_pos=0)]})
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Fecha", data_type="DATE", pk=True)]), c)
    (col,) = _cols(plan)
    assert (col["payload"]["pkPosition"], col["payload"]["ordinal"]) == (1, 1)


def test_pk_quitada_en_la_hoja_avisa():
    c = ctx(schemas=_SCHEMAS, tables=[_table()], columns_by_table={"t1": [_col("c1", "COD", "Codigo", 0, pk=True, pk_pos=0)]})
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Codigo", physical="COD")]), c)
    (col,) = _cols(plan)
    assert (col["payload"]["isPrimaryKey"], col["payload"]["pkPosition"], col["payload"]["isNullable"]) == (None, None, False)
    assert codes(plan, "warning") == ["pk-removed", "existing-column"]


def test_duplicados_de_columna_en_el_archivo():
    p = parsed(tables=[trow(3, "Cliente", schema="ddv")],
               columns=[crow(3, "Cliente", "Codigo", data_type="STRING"),
                        crow(4, "Cliente", "codigo", data_type="STRING"),
                        crow(5, "Cliente", "Otro", physical="CODIGO", data_type="STRING")])
    plan = build_plan(p, ctx(schemas=_SCHEMAS))
    assert [(e["code"], e["row"]) for e in plan.report["errors"]] == [("duplicate-in-file", 4), ("duplicate-in-file", 5)]
    assert len(_cols(plan)) == 1


def test_particion_por_udp_marca_is_partition():
    defs = [{"id": "u2", "name": "Particion", "level": "column", "dataType": "list", "defaultValue": "No Definido",
             "allowedValues": ["No Definido", "PART_01", "PART_02"]}]
    p = parsed(tables=[trow(3, "Cliente", schema="ddv")],
               columns=[crow(3, "Cliente", "Mes", data_type="INTEGER", udp={"UDP_Particion": "part_01"}),
                        crow(4, "Cliente", "Dia", data_type="INTEGER", udp={"UDP_Particion": ""}),
                        crow(5, "Cliente", "Ano", data_type="INTEGER", udp={"UDP_Particion": "no definido"})],
               column_udp={"UDP_Particion": defs})
    plan = build_plan(p, ctx(schemas=_SCHEMAS, udp_defs=defs))
    mes, dia, ano = (c["payload"] for c in _cols(plan))
    assert (mes["isPartition"], mes["udpValues"]) == (True, {"u2": "PART_01"})
    assert (dia["isPartition"], dia["udpValues"]) == (False, {"u2": "No Definido"})
    assert (ano["isPartition"], ano["udpValues"]) == (False, {"u2": "No Definido"})


def test_udp_de_columna_valida_contra_la_lista():
    defs = [{"id": "u3", "name": "Clasificacion del Dato", "level": "column", "dataType": "list",
             "defaultValue": "No Definido", "allowedValues": ["No Definido", "No DAC", "DAC-NOMBRE"]}]
    p = parsed(tables=[trow(3, "Cliente", schema="ddv")],
               columns=[crow(3, "Cliente", "A", data_type="STRING", udp={"UDP_Clasificacion_del_Dato": "dac-nombre"}),
                        crow(4, "Cliente", "B", data_type="STRING", udp={"UDP_Clasificacion_del_Dato": "DAC-X"})],
               column_udp={"UDP_Clasificacion_del_Dato": defs})
    plan = build_plan(p, ctx(schemas=_SCHEMAS, udp_defs=defs))
    (a,) = (c["payload"] for c in _cols(plan))
    assert a["udpValues"] == {"u3": "DAC-NOMBRE"}
    (e,) = plan.report["errors"]
    assert (e["code"], e["row"], e["column"]) == ("invalid-udp-value", 4, "UDP_Clasificacion_del_Dato")


def test_cambio_de_dominio_reaplica_el_tipo_del_nuevo():
    c = ctx(schemas=_SCHEMAS, domains=_DOMAINS, tables=[_table()],
            columns_by_table={"t1": [_col("c1", "MONTO", "Monto", 0, parentDomainId="d1")]})
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Monto", domain="Monto")]), c)
    (col,) = _cols(plan)
    assert (col["payload"]["parentDomainId"], col["payload"]["dataType"], col["payload"]["typeOverridden"]) == ("d2", "DECIMAL(18,2)", False)


def test_nombre_fisico_de_columna_largo_es_error():
    c = ctx(schemas=_SCHEMAS, naming=naming(max_len=5))
    p = parsed(tables=[trow(3, "Cli", schema="ddv")], columns=[crow(3, "Cli", "Codigo Cliente", data_type="STRING")])
    plan = build_plan(p, c)
    assert [(e["sheet"], e["code"], e["column"]) for e in plan.report["errors"]] == [("Atributos", "name-too-long", "CAMPO_LOGICO")]


def test_columnas_de_una_fila_de_tabla_con_error_se_validan_igual():
    # La tabla sin ESQUEMA es error, pero las columnas se validan para dar
    # TODO el feedback en una pasada (nada se escribe mientras haya errores).
    p = parsed(tables=[trow(3, "Cliente")], columns=[crow(3, "Cliente", "A", data_type="texto raro")])
    plan = build_plan(p, ctx())
    assert sorted(e["code"] for e in plan.report["errors"]) == ["invalid-type", "missing-required"]


def test_ordinal_pk_primero_en_tabla_nueva_doc81():
    """Doc 81: en una tabla NUEVA el `ordinal` nace con las PK primero (en orden
    de hoja = orden de llave), aunque en la hoja haya una no-PK intercalada.
    Reproduce el bug reportado: hoja [PK, PK, no-PK, PK] guardaba el ordinal en
    orden de hoja y el properties (puro ordinal) divergía del canvas (PK-first).
    """
    p = parsed(tables=[trow(3, "Cuenta", schema="ddv")],
               columns=[crow(3, "Cuenta", "Codigo Clave Party Cliente", data_type="varchar(128)", pk=True),
                        crow(4, "Cuenta", "Tipo Rol Cliente", data_type="varchar(128)", pk=True),
                        crow(5, "Cuenta", "xd", data_type="varchar(128)"),
                        crow(6, "Cuenta", "Codigo Clave Cuenta Evaluada", data_type="varchar(128)", pk=True)])
    plan = build_plan(p, ctx(schemas=_SCHEMAS, domains=_DOMAINS), new_id=seq_ids())
    assert plan.has_errors is False
    by_logical = {c["payload"]["logicalName"]: c["payload"] for c in _cols(plan)}
    ordinal = {lg: pl["ordinal"] for lg, pl in by_logical.items()}
    # PKs primero (orden de hoja entre PKs), la no-PK al final.
    assert ordinal == {"Codigo Clave Party Cliente": 0, "Tipo Rol Cliente": 1,
                       "Codigo Clave Cuenta Evaluada": 2, "xd": 3}
    # pkPosition = orden de llave (mismo orden de hoja entre PKs).
    assert by_logical["Codigo Clave Party Cliente"]["pkPosition"] == 0
    assert by_logical["Tipo Rol Cliente"]["pkPosition"] == 1
    assert by_logical["Codigo Clave Cuenta Evaluada"]["pkPosition"] == 2
    assert by_logical["xd"]["isPrimaryKey"] in (None, False)

