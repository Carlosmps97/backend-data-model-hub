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
    # Oracle no tiene nada de Databricks
    assert not {"location", "tblProperties", "createTable", "tableFormat", "includePartitions"} & set(o)
    # el efectivo es una COPIA: editarlo no ensucia los defaults del módulo
    eff = out_mod.effective_output(None)
    eff["oracle"]["typeCase"] = "lower"
    assert out_mod.OUTPUT_DEFAULTS["oracle"]["typeCase"] == "upper"
    assert out_mod.ORACLE_DEFAULTS["typeCase"] == "upper"


def test_normalize_doc101_dialecto_y_oracle():
    clean = out_mod.normalize_output({
        "dialect": "oracle",
        "oracle": {"typeCase": "lower", "includeKeys": False, "defaultSchema": "  bcp  ", "zzz": 1},
    })
    assert clean == {"dialect": "oracle", "oracle": {
        "typeCase": "lower", "includeKeys": False, "defaultSchema": "bcp"}}
    with pytest.raises(ValueError, match="dialect"):
        out_mod.normalize_output({"dialect": "postgres"})
    with pytest.raises(ValueError, match="identifierCase"):
        out_mod.normalize_output({"oracle": {"identifierCase": "camel"}})
    with pytest.raises(ValueError, match="includeComments"):
        out_mod.normalize_output({"oracle": {"includeComments": "no"}})
    with pytest.raises(ValueError, match="must be an object"):
        out_mod.normalize_output({"oracle": ["x"]})


def test_effective_doc101_oracle_clave_a_clave_y_proyecto_viejo():
    """Un proyecto guardado ANTES del doc 101 (sin `dialect` ni `oracle`) abre en
    Databricks con los defaults de Oracle; un valor viejo inválido cae a su default."""
    viejo = out_mod.effective_output({"typeCase": "upper", "includeKeys": True})
    assert viejo["dialect"] == "databricks" and viejo["oracle"] == out_mod.ORACLE_DEFAULTS
    eff = out_mod.effective_output({"dialect": "oracle",
                                    "oracle": {"typeCase": "raro", "includeComments": True}})
    assert eff["dialect"] == "oracle"
    assert eff["oracle"]["typeCase"] == "upper" and eff["oracle"]["includeComments"] is True
    assert out_mod.effective_output({"oracle": "x"})["oracle"] == out_mod.ORACLE_DEFAULTS


def test_doc108_oracle_not_null_aparte_y_esquema():
    """Doc 108 (como Erwin): NOT NULL es un interruptor aparte de las llaves y
    apagado; el esquema de los nombres: ninguno (default), el del modelo o uno
    elegido. Espejo de DEFAULT_ORACLE_OPTS del front."""
    o = out_mod.ORACLE_DEFAULTS
    assert o["includeNotNull"] is False and o["schemaMode"] == "none" and o["includeKeys"] is True
    clean = out_mod.normalize_output({"oracle": {"includeNotNull": True, "schemaMode": "custom", "defaultSchema": "bcp"}})
    assert clean == {"oracle": {"includeNotNull": True, "schemaMode": "custom", "defaultSchema": "bcp"}}
    with pytest.raises(ValueError, match="schemaMode"):
        out_mod.normalize_output({"oracle": {"schemaMode": "raro"}})
    with pytest.raises(ValueError, match="includeNotNull"):
        out_mod.normalize_output({"oracle": {"includeNotNull": "si"}})
    eff = out_mod.effective_output({"dialect": "oracle", "oracle": {"schemaMode": "raro", "typeCase": "lower"}})
    assert eff["oracle"]["schemaMode"] == "none" and eff["oracle"]["typeCase"] == "lower"
    # un proyecto guardado antes del doc 108 toma los defaults nuevos
    assert out_mod.effective_output({"oracle": {"includeKeys": True}})["oracle"]["schemaMode"] == "none"


def test_apply_doc101_guarda_oracle_y_rechaza_invalidos(monkeypatch):
    set_cfg = _mock_apply(monkeypatch)
    asyncio.run(std.apply("ana", "p1", ApplyBody(kind="ddl", ddlConfigPatch=DdlConfigPatch(
        output={"dialect": "oracle", "oracle": {"typeCase": "upper"}}))))
    set_cfg.assert_awaited_once_with("p1", lookups=None, functions=None,
                                     output={"dialect": "oracle", "oracle": {"typeCase": "upper"}})
    with pytest.raises(HTTPException) as e:
        asyncio.run(std.apply("ana", "p1", ApplyBody(kind="ddl", ddlConfigPatch=DdlConfigPatch(
            output={"oracle": {"quoteIdentifiers": "never"}}))))
    assert e.value.status_code == 422 and "quoteIdentifiers" in e.value.detail


# ── Doc 106: el export Oracle sale tal cual (sin mapeo de tipos ni tamaño máximo) ──


def test_doc106_oracle_lo_guardado_por_una_version_anterior_se_ignora():
    """El mapeo de tipos y el tamaño máximo de strings ya no existen: lo que dejó
    guardado una versión anterior se descarta sin error y no vuelve en el efectivo."""
    viejo = {"typeCase": "lower", "maxStringSize": "extended",
             "typeMap": [{"from": "DATETIME", "to": "DATE"}, {"from": "DATETIME"}]}
    assert out_mod.normalize_output({"oracle": viejo}) == {"oracle": {"typeCase": "lower"}}
    eff = out_mod.effective_output({"dialect": "oracle", "oracle": viejo})["oracle"]
    assert eff == {**out_mod.ORACLE_DEFAULTS, "typeCase": "lower"}
    assert not {"typeMap", "maxStringSize"} & set(eff)


def test_doc106_apply_con_settings_de_una_version_anterior_no_da_422(monkeypatch):
    set_cfg = _mock_apply(monkeypatch)
    asyncio.run(std.apply("ana", "p1", ApplyBody(kind="ddl", ddlConfigPatch=DdlConfigPatch(
        output={"oracle": {"typeCase": "upper", "typeMap": [{"from": "DATETIME"}], "maxStringSize": "huge"}}))))
    set_cfg.assert_awaited_once_with("p1", lookups=None, functions=None, output={"oracle": {"typeCase": "upper"}})
