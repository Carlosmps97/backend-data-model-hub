"""Doc 74: orden ÚNICO de columnas que hereda la migración — llaves primarias
primero (en el orden de la llave) y después el resto en el Column order de
Erwin. Es lo que el owner llama el orden «físico normal» (PK al inicio)."""
from __future__ import annotations

from scripts.erwin_migration import erwin_parser as ep
from scripts.erwin_migration import policies as pol


def _attr(aid, order, column_order):
    return ep.ErwinAttribute(
        id=aid, owner_id="E1", owner_kind="Entity", name=aid.lower(), physical=aid, physical_raw=aid,
        data_type="STRING", logical_type="", nullable=True, order=order, definition="", comment="",
        domain_ref=None, parent_attr_ref=None, parent_rel_ref=None, column_order=column_order)


def test_sin_llaves_manda_el_column_order_y_desempata_el_fisico():
    attrs = [_attr("A", 1, 2), _attr("B", 2, 0), _attr("C", 3, 1), _attr("D", 4, 1)]
    assert [a.id for a in pol.column_order(attrs, set(), [])] == ["B", "C", "D", "A"]


def test_las_llaves_suben_al_inicio_en_el_orden_de_la_llave():
    # Column order: A(0) K2(1) B(2) K1(3) — llave: K1, K2 ⇒ K1 K2 A B
    attrs = [_attr("A", 1, 0), _attr("K2", 2, 1), _attr("B", 3, 2), _attr("K1", 4, 3)]
    out = pol.column_order(attrs, {"K1", "K2"}, ["K1", "K2"])
    assert [a.id for a in out] == ["K1", "K2", "A", "B"]


def test_pk_sin_posicion_en_la_llave_va_tras_las_ordenadas_y_antes_del_resto():
    attrs = [_attr("A", 1, 0), _attr("K2", 2, 1), _attr("K1", 3, 2)]
    out = pol.column_order(attrs, {"K1", "K2"}, ["K2"])
    assert [a.id for a in out] == ["K2", "K1", "A"]


def test_devuelve_lista_nueva_sin_mutar_la_entrada():
    attrs = [_attr("B", 2, 1), _attr("A", 1, 0)]
    out = pol.column_order(attrs, set(), [])
    assert [a.id for a in out] == ["A", "B"] and [a.id for a in attrs] == ["B", "A"]
    assert pol.column_order([], set(), []) == []
