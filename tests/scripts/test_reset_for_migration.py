"""wipe() destructivo (doc 54) con FakeDb — dropea TODO, sin lista blanca."""
from __future__ import annotations

from scripts.reset_for_migration import wipe


class _Coll:
    def __init__(self, n: int) -> None:
        self.n = n
        self.dropped = False

    def count_documents(self, _q) -> int:
        return self.n

    def drop(self) -> None:
        self.dropped = True


class _Db:
    def __init__(self, colls: dict[str, _Coll]) -> None:
        self.colls = colls

    def list_collection_names(self) -> list[str]:
        return list(self.colls)

    def __getitem__(self, name: str) -> _Coll:
        return self.colls[name]


def test_dry_run_cuenta_y_no_borra():
    db = _Db({"users": _Coll(3), "column_catalog": _Coll(7)})
    assert wipe(db, apply=False) == (2, 10)
    assert not any(c.dropped for c in db.colls.values())


def test_apply_dropea_todo_incluidas_column_catalog_y_vestigiales():
    db = _Db({"projects": _Coll(1), "users": _Coll(2), "roles": _Coll(4),
              "audit_log": _Coll(9), "column_catalog": _Coll(5),
              "tabla_vieja_x": _Coll(0)})
    assert wipe(db, apply=True) == (6, 21)
    assert all(c.dropped for c in db.colls.values())
