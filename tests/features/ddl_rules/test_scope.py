"""Doc 75 D3/I5: config del ruleset por proyecto y restore acotado."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.features.ddl_rules import repository


def test_config_por_proyecto_y_restore_acotado(monkeypatch):
    rules = MagicMock(); rules.update_many = AsyncMock(); rules.bulk_write = AsyncMock()
    cfg = MagicMock(); cfg.find_one = AsyncMock(return_value=None); cfg.update_one = AsyncMock()
    monkeypatch.setattr(repository, "get_db", AsyncMock(return_value={"ddl_rules": rules, "ddl_ruleset_config": cfg}))
    out = asyncio.run(repository.get_config("p1"))
    cfg.find_one.assert_awaited_once_with({"_id": "p1"})
    assert out["id"] == "p1" and out["projectId"] == "p1" and out["lookups"] == {}
    asyncio.run(repository.restore_rules("p1", []))
    assert rules.update_many.call_args.args[0]["projectId"] == "p1"
    asyncio.run(repository.restore_config("p1", {"lookups": {"a": {}}, "functions": []}))
    assert cfg.update_one.call_args.args[0] == {"_id": "p1"}
    assert cfg.update_one.call_args.args[1]["$set"]["projectId"] == "p1"
