"""Doc 93 D1 · Output settings del Export DDL: dato del proyecto
(`ddl_ruleset_config.output`), versionado en Data Standards (apply, snapshot,
diff, rollback y copia), tolerante con valores viejos/inválidos."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.features.data_standards import service as std
from app.features.data_standards.schemas import ApplyBody, DdlConfigPatch
from app.features.ddl_rules import output as out_mod
from app.features.ddl_rules import repository


def test_defaults_son_la_macro_bcp():
    d = out_mod.OUTPUT_DEFAULTS
    assert d["quoteIdentifiers"] == "when-needed" and d["typeCase"] == "lower"
    assert d["createTable"] == "or-replace" and d["viewTagsAs"] == "table" and d["includeKeys"] is False
    assert d["location"] == "abfss://<container_name>@<storage_account_name>.dfs.core.windows.net/<path>"
    assert d["fileNames"] == {"table": "TABLE", "modeledView": "VIEW_NEG", "generatedView": "VIEW_TEC",
                              "separator": "-", "nameCase": "upper", "schema": "when-needed"}


def test_normalize_limpia_y_valida():
    clean = out_mod.normalize_output({"typeCase": "upper", "zzz": 1, "catalog": "  main  ",
                                      "tblProperties": [{"key": " a ", "value": 1}, {"key": ""}],
                                      "fileNames": {"table": "TBL", "otra": "x"}})
    assert clean == {"typeCase": "upper", "catalog": "main",
                     "tblProperties": [{"key": "a", "value": "1"}], "fileNames": {"table": "TBL"}}
    with pytest.raises(ValueError, match="createTable"):
        out_mod.normalize_output({"createTable": "replace"})
    with pytest.raises(ValueError, match="external"):
        out_mod.normalize_output({"external": "yes"})
    with pytest.raises(ValueError, match="can't contain"):
        out_mod.normalize_output({"fileNames": {"table": "TA/BLE"}})


def test_effective_output_tolera_claves_invalidas_una_por_una():
    """Review Focus #5: un valor guardado inválido cae al default; el resto vale."""
    eff = out_mod.effective_output({"typeCase": "raro", "viewTagsAs": "view", "fileNames": {"modeledView": "NEG"}})
    assert eff["typeCase"] == "lower" and eff["viewTagsAs"] == "view"
    assert eff["fileNames"]["modeledView"] == "NEG" and eff["fileNames"]["table"] == "TABLE"
    assert out_mod.effective_output(None) == out_mod.OUTPUT_DEFAULTS


def test_snapshot_y_diff_incluyen_output():
    snap = std.snapshot_of([], [], {}, [], [], {"lookups": {}, "functions": [], "output": {"typeCase": "upper"}})
    assert snap["ddlConfig"] == {"lookups": {}, "functions": [], "output": {"typeCase": "upper"}}
    assert std.snapshot_of([], [], {})["ddlConfig"] == {"lookups": {}, "functions": [], "output": {}}
    diff = std.build_diff(ApplyBody(kind="ddl", ddlConfigPatch=DdlConfigPatch(output={"typeCase": "upper"})), {}, {})
    assert "DDL output settings" in diff["edited"]


def _mock_apply(monkeypatch):
    for repo, fn, val in ((std.dom_repo, "list_domains", []), (std.dict_repo, "list_entries", []),
                          (std.udp_repo, "list_udp", []), (std.rules_repo, "list_rules", [])):
        monkeypatch.setattr(repo, fn, AsyncMock(return_value=val))
    monkeypatch.setattr(std.rules_repo, "get_config", AsyncMock(return_value={"lookups": {}, "functions": [], "output": {}}))
    set_cfg = AsyncMock()
    monkeypatch.setattr(std.rules_repo, "set_config", set_cfg)
    monkeypatch.setattr(std, "current_snapshot", AsyncMock(return_value={}))
    monkeypatch.setattr(std.repository, "insert_version_next_seq",
                        AsyncMock(side_effect=lambda pid, f: {**f, "seq": 3, "label": "v3", "id": "v3"}))
    monkeypatch.setattr(std, "audit", AsyncMock())
    return set_cfg


def test_apply_guarda_output_normalizado_y_rechaza_invalidos(monkeypatch):
    set_cfg = _mock_apply(monkeypatch)
    asyncio.run(std.apply("ana", "p1", ApplyBody(kind="ddl", ddlConfigPatch=DdlConfigPatch(
        output={"viewTagsAs": "view", "basura": 1}))))
    set_cfg.assert_awaited_once_with("p1", lookups=None, functions=None, output={"viewTagsAs": "view"})
    with pytest.raises(HTTPException) as e:
        asyncio.run(std.apply("ana", "p1", ApplyBody(kind="ddl", ddlConfigPatch=DdlConfigPatch(
            output={"typeCase": "raro"}))))
    assert e.value.status_code == 422 and "typeCase" in e.value.detail


def test_repository_set_y_restore_escriben_output(monkeypatch):
    cfg = MagicMock()
    cfg.find_one = AsyncMock(return_value=None)
    cfg.update_one = AsyncMock()
    monkeypatch.setattr(repository, "get_db", AsyncMock(return_value={"ddl_ruleset_config": cfg}))
    asyncio.run(repository.set_config("p1", output={"typeCase": "upper"}))
    assert cfg.update_one.call_args.args[1]["$set"]["output"] == {"typeCase": "upper"}
    asyncio.run(repository.restore_config("p1", {"lookups": {}, "functions": [], "output": {"a": 1}}))
    assert cfg.update_one.call_args.args[1]["$set"]["output"] == {"a": 1}
    asyncio.run(repository.restore_config("p1", {}))                      # snapshot pre-doc 93 → vacío
    assert cfg.update_one.call_args.args[1]["$set"]["output"] == {}


def test_templates_payload_trae_seed_output(monkeypatch):
    from app.features.ddl_rules import service as svc
    monkeypatch.setattr(svc.udp_repo, "list_udp", AsyncMock(return_value=[]))
    payload = asyncio.run(svc.templates_payload("p1"))
    assert payload["seedOutput"] == out_mod.OUTPUT_DEFAULTS
    payload["seedOutput"]["typeCase"] = "upper"                           # copia: no ensucia el módulo
    assert out_mod.OUTPUT_DEFAULTS["typeCase"] == "lower"


def test_bench_usa_las_output_settings_del_proyecto(monkeypatch):
    from app.features.ddl_rules import service as svc
    rule = {"id": "t", "name": "t", "kind": "rule", "target": "table", "condition": "",
            "action": {"tags": {"k": "v"}}, "appliesTo": ["ddl.vista_negocio"], "priority": 1, "enabled": True}
    table = {"id": "t1", "physicalName": "T", "schema": "s", "udpValues": {}}
    monkeypatch.setattr(svc.udp_repo, "list_udp", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc.repository, "get_config", AsyncMock(return_value={
        "lookups": {}, "functions": [], "output": {"viewTagsAs": "view", "quoteIdentifiers": "always"}}))
    monkeypatch.setattr(svc.repository, "list_rules", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc.dom_repo, "list_domains", AsyncMock(return_value=[]))
    monkeypatch.setattr(svc.catalog_repo, "list_tables_by_ids", AsyncMock(return_value=[table]))
    monkeypatch.setattr(svc.catalog_repo, "list_columns", AsyncMock(return_value=[]))
    frag = asyncio.run(svc.test_rule("p1", rule, "t1"))["fragments"][0]
    assert frag["sql"] == "ALTER VIEW `s`.`t` SET TAGS ('k' = 'v');"


# ── Doc 101: dialecto por default + configuración propia de Oracle ─────────


def test_defaults_doc101_dialecto_databricks_y_oracle_propio():
    d = out_mod.OUTPUT_DEFAULTS
    assert d["dialect"] == "databricks"
    o = d["oracle"]
    assert (o["includeComments"], o["includeKeys"], o["includeViews"]) == (False, True, True)
    assert (o["typeCase"], o["identifierCase"], o["quoteIdentifiers"]) == ("upper", "upper", "when-needed")
    assert o["defaultSchema"] == ""
    assert o["maxStringSize"] == "standard"
    # la lista EXACTA — espejo de DEFAULT_ORACLE_TYPE_MAP del front (oracle.test.ts)
    assert [f"{m['from']}→{m['to']}" for m in o["typeMap"]] == [
        "DATETIME→DATE", "STRING→VARCHAR2(4000)", "VARCHAR→VARCHAR(4000)", "VARCHAR2→VARCHAR2(4000)",
        "VARCHAR(MAX)→CLOB", "NVARCHAR→NVARCHAR2(2000)", "NVARCHAR(MAX)→NCLOB", "TEXT→CLOB",
        "DECIMAL→DECIMAL(38,18)", "NUMERIC→NUMERIC(38,18)", "BIGINT→NUMBER(19)", "TINYINT→NUMBER(3)",
        "DOUBLE→BINARY_DOUBLE", "MONEY→NUMBER(19,4)", "BOOLEAN→NUMBER(1)",
        "TIMESTAMPTZ→TIMESTAMP WITH TIME ZONE", "TIME→DATE", "BINARY→BLOB", "VARBINARY→RAW(2000)",
        "VARBINARY(MAX)→BLOB", "BYTES→BLOB", "JSON→CLOB", "JSONB→CLOB", "VARIANT→CLOB", "XML→XMLTYPE",
        "UUID→VARCHAR2(36)", "ARRAY→CLOB", "MAP→CLOB", "STRUCT→CLOB"]
    with pytest.raises(ValueError, match="maxStringSize"):
        out_mod.normalize_output({"oracle": {"maxStringSize": "huge"}})
    assert out_mod.normalize_output({"oracle": {"maxStringSize": "extended"}}) == {"oracle": {"maxStringSize": "extended"}}
    # Oracle no tiene nada de Databricks
    assert not {"location", "tblProperties", "createTable", "tableFormat", "includePartitions"} & set(o)
    # el efectivo es una COPIA: editarlo no ensucia los defaults del módulo
    eff = out_mod.effective_output(None)
    eff["oracle"]["typeMap"].append({"from": "X", "to": "Y"})
    assert {"from": "X", "to": "Y"} not in out_mod.OUTPUT_DEFAULTS["oracle"]["typeMap"]
    assert {"from": "X", "to": "Y"} not in out_mod.ORACLE_DEFAULTS["typeMap"]
    assert out_mod.OUTPUT_DEFAULTS["oracle"]["typeMap"] is not out_mod.ORACLE_DEFAULTS["typeMap"]


def test_normalize_doc101_dialecto_y_oracle():
    clean = out_mod.normalize_output({
        "dialect": "oracle",
        "oracle": {"typeCase": "lower", "includeKeys": False, "defaultSchema": "  bcp  ", "zzz": 1,
                   "typeMap": [{"from": " datetime ", "to": "TIMESTAMP"}, {"from": "", "to": ""},
                               {"from": "TIMESTAMPTZ", "to": "TIMESTAMP   WITH TIME ZONE"}]},
    })
    assert clean == {"dialect": "oracle", "oracle": {
        "typeCase": "lower", "includeKeys": False, "defaultSchema": "bcp",
        "typeMap": [{"from": "datetime", "to": "TIMESTAMP"},
                    {"from": "TIMESTAMPTZ", "to": "TIMESTAMP WITH TIME ZONE"}]}}
    with pytest.raises(ValueError, match="dialect"):
        out_mod.normalize_output({"dialect": "postgres"})
    with pytest.raises(ValueError, match="identifierCase"):
        out_mod.normalize_output({"oracle": {"identifierCase": "camel"}})
    with pytest.raises(ValueError, match="includeComments"):
        out_mod.normalize_output({"oracle": {"includeComments": "no"}})
    with pytest.raises(ValueError, match="must be an object"):
        out_mod.normalize_output({"oracle": ["x"]})
    with pytest.raises(ValueError, match="the Oracle type is missing"):
        out_mod.normalize_output({"oracle": {"typeMap": [{"from": "DATETIME", "to": " "}]}})
    with pytest.raises(ValueError, match="isn't a valid Oracle type"):
        out_mod.normalize_output({"oracle": {"typeMap": [{"from": "DATE", "to": "DATE; DROP TABLE x"}]}})
    with pytest.raises(ValueError, match="list of"):
        out_mod.normalize_output({"oracle": {"typeMap": "DATETIME=DATE"}})


def test_effective_doc101_oracle_clave_a_clave_y_proyecto_viejo():
    """Un proyecto guardado ANTES del doc 101 (sin `dialect` ni `oracle`) abre en
    Databricks con los defaults de Oracle; un valor viejo inválido cae a su default."""
    viejo = out_mod.effective_output({"typeCase": "upper", "includeKeys": True})
    assert viejo["dialect"] == "databricks" and viejo["oracle"] == out_mod.ORACLE_DEFAULTS
    eff = out_mod.effective_output({"dialect": "oracle",
                                    "oracle": {"typeCase": "raro", "includeComments": True, "typeMap": []}})
    assert eff["dialect"] == "oracle"
    assert eff["oracle"]["typeCase"] == "upper" and eff["oracle"]["includeComments"] is True
    assert eff["oracle"]["typeMap"] == []                 # lista vacía guardada = sin mapeo (tipos tal cual)
    assert out_mod.effective_output({"oracle": "x"})["oracle"] == out_mod.ORACLE_DEFAULTS


def test_apply_doc101_guarda_oracle_y_rechaza_mapeo_invalido(monkeypatch):
    set_cfg = _mock_apply(monkeypatch)
    asyncio.run(std.apply("ana", "p1", ApplyBody(kind="ddl", ddlConfigPatch=DdlConfigPatch(
        output={"dialect": "oracle", "oracle": {"typeCase": "upper"}}))))
    set_cfg.assert_awaited_once_with("p1", lookups=None, functions=None,
                                     output={"dialect": "oracle", "oracle": {"typeCase": "upper"}})
    with pytest.raises(HTTPException) as e:
        asyncio.run(std.apply("ana", "p1", ApplyBody(kind="ddl", ddlConfigPatch=DdlConfigPatch(
            output={"oracle": {"typeMap": [{"from": "DATETIME"}]}}))))
    assert e.value.status_code == 422 and "the Oracle type is missing" in e.value.detail


def test_revision_doc101_mapeo_sin_origenes_repetidos():
    """Revisión independiente #2: dos filas con el mismo origen (sin distinguir
    mayúsculas ni espacios) — la segunda nunca aplicaría (gana la primera)."""
    with pytest.raises(ValueError, match=r"'DATETIME' is mapped more than once \(rows 1 and 3\)"):
        out_mod.normalize_output({"oracle": {"typeMap": [
            {"from": "DATETIME", "to": "DATE"}, {"from": "STRING", "to": "CLOB"},
            {"from": " date time ", "to": "TIMESTAMP"}]}})
    # con y sin largo son orígenes distintos
    ok = out_mod.normalize_output({"oracle": {"typeMap": [
        {"from": "VARCHAR", "to": "VARCHAR2(4000)"}, {"from": "VARCHAR(MAX)", "to": "CLOB"}]}})
    assert len(ok["oracle"]["typeMap"]) == 2


def test_revision_doc101_tipos_oracle_validos_con_asterisco_punto_y_signo():
    """Revisión independiente #3: `NUMBER(*,2)`, `SYS.XMLTYPE`, escala negativa."""
    ok = out_mod.normalize_output({"oracle": {"typeMap": [
        {"from": "DECIMAL", "to": "NUMBER(*,2)"}, {"from": "XML", "to": "SYS.XMLTYPE"},
        {"from": "MONEY", "to": "NUMBER(5,-2)"}]}})
    assert [m["to"] for m in ok["oracle"]["typeMap"]] == ["NUMBER(*,2)", "SYS.XMLTYPE", "NUMBER(5,-2)"]
    for bad in ("DATE -- x", "DATE; DROP TABLE x", "DATE'", 'DATE"', "X" * 101):
        with pytest.raises(ValueError, match="row 1"):
            out_mod.normalize_output({"oracle": {"typeMap": [{"from": "DATETIME", "to": bad}]}})


def test_revision_doc101_mensajes_con_la_fila_y_las_etiquetas_de_la_ui():
    """Revisión independiente #5: la fila y el nombre de la caja que ve el usuario."""
    with pytest.raises(ValueError, match=r"row 2: the Oracle type is missing"):
        out_mod.normalize_output({"oracle": {"typeMap": [{"from": "A", "to": "B"}, {"from": "DATETIME", "to": ""}]}})
    with pytest.raises(ValueError, match=r"row 1: the Model type is missing"):
        out_mod.normalize_output({"oracle": {"typeMap": [{"from": " ", "to": "DATE"}]}})
    with pytest.raises(ValueError, match=r"row 1: the Oracle type is longer than 100 characters"):
        out_mod.normalize_output({"oracle": {"typeMap": [{"from": "DATETIME", "to": "X" * 101}]}})
    with pytest.raises(ValueError, match=r"row 1: 'DATE; X' isn't a valid Oracle type"):
        out_mod.normalize_output({"oracle": {"typeMap": [{"from": "DATETIME", "to": "DATE; X"}]}})
