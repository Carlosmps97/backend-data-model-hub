"""`display_order` (doc 81 → doc 94, espejo de `columnOrder.ts`): el orden
ÚNICO de una tabla — PK primero y cada bloque por `ordinal`. No hay orden de
llave aparte (un `pkPosition` viejo se ignora). Estable y sin mutar la entrada."""
from __future__ import annotations

from app.core.column_order import display_order


def _c(cid: str, ordinal: int, pk: bool = False, pos: int | None = None) -> dict:
    return {"id": cid, "ordinal": ordinal, "isPrimaryKey": True if pk else None, "pkPosition": pos}


def _ids(cols: list[dict]) -> list[str]:
    return [c["id"] for c in cols]


def test_pk_primero_y_cada_bloque_por_ordinal():
    cols = [_c("a", 0), _c("k2", 1, pk=True), _c("b", 2), _c("k1", 3, pk=True)]
    assert _ids(display_order(cols)) == ["k2", "k1", "a", "b"]


def test_un_pk_position_viejo_no_manda():
    cols = [_c("k-b", 0, pk=True, pos=1), _c("k-a", 5, pk=True, pos=0), _c("x", 1)]
    assert _ids(display_order(cols)) == ["k-b", "k-a", "x"]


def test_sin_pk_es_puro_ordinal_y_ordinal_ausente_vale_cero():
    cols = [{"id": "b", "ordinal": 2}, {"id": "z"}, {"id": "a", "ordinal": 1}]
    assert _ids(display_order(cols)) == ["z", "a", "b"]


def test_estable_y_no_muta_la_entrada():
    cols = [_c("a", 1), _c("b", 1), _c("c", 0)]
    before = [dict(c) for c in cols]
    assert _ids(display_order(cols)) == ["c", "a", "b"]
    assert cols == before
    assert display_order([]) == []
