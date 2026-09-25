"""Doc 94 D11 · `wordType` retirado del glosario: el snapshot no lo lleva, la
comparación de contenido y la clave de naming lo ignoran (un rollback a una
versión vieja no da 409 ni re-deriva todo) y el restore no lo reinyecta."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.features.data_standards import repository as std_repo
from app.features.data_standards import service as std

OLD = {"id": "t1", "term": "cliente", "abbrev": "CLI", "scope": "column", "wordType": "prime",
       "locked": False, "lockedBy": None, "lockedAt": None}          # snapshot previo al doc 94
CUR = {"id": "t1", "term": "cliente", "abbrev": "CLI", "scope": "column",
       "locked": True, "lockedBy": "ana", "lockedAt": "2026-09-25"}   # hoy: bloqueado, sin wordType


def test_snapshot_of_no_lleva_wordtype():
    snap = std.snapshot_of([], [{**CUR, "wordType": "class"}], {})
    assert "wordType" not in snap["dict"][0]
    assert snap["dict"][0]["term"] == "cliente"


def test_rollback_a_version_vieja_no_toca_el_bloqueado_por_wordtype():
    assert std.locked_terms_touched([OLD], [CUR]) == []
    assert std.locked_ids_preserved([OLD], [CUR]) == {"t1"}


def test_naming_key_ignora_wordtype_y_el_lock():
    assert std._naming_key({"dict": [OLD], "namingConfig": {}}) == std._naming_key({"dict": [CUR], "namingConfig": {}})
    changed = {**CUR, "abbrev": "CLTE"}
    assert std._naming_key({"dict": [OLD], "namingConfig": {}}) != std._naming_key({"dict": [changed], "namingConfig": {}})


def test_restore_dict_no_reinyecta_campos_retirados(monkeypatch):
    coll = MagicMock()
    coll.update_many = AsyncMock()
    coll.bulk_write = AsyncMock()
    monkeypatch.setattr(std_repo, "get_db", AsyncMock(return_value={std_repo.DICT: coll}))
    asyncio.run(std_repo.restore_dict("p1", [{**OLD, "zzz": 1}]))
    op = coll.bulk_write.call_args.args[0][0]
    written = op._doc["$set"]
    assert "wordType" not in written and "zzz" not in written
    assert (written["term"], written["abbrev"], written["scope"], written["projectId"]) == ("cliente", "CLI", "column", "p1")
