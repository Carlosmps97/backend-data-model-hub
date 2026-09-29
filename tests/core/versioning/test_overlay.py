"""Overlay (publicado + cambios del changeset) y diff — puro."""
from __future__ import annotations

from app.core.versioning.overlay import overlay, summarize_diff

PUB = [{"id": "a", "name": "uno"}, {"id": "b", "name": "dos"}]


def test_overlay_upsert_modifica_existente():
    out = overlay(PUB, {"a": {"op": "upsert", "payload": {"name": "UNO"}}})
    assert {e["id"]: e["name"] for e in out} == {"a": "UNO", "b": "dos"}


def test_overlay_upsert_agrega_nuevo():
    out = overlay(PUB, {"c": {"op": "upsert", "payload": {"name": "tres"}}})
    assert {e["id"] for e in out} == {"a", "b", "c"}


def test_overlay_delete_quita():
    out = overlay(PUB, {"b": {"op": "delete"}})
    assert {e["id"] for e in out} == {"a"}


def test_overlay_sin_cambios_es_identidad():
    assert overlay(PUB, {}) == PUB


def test_summarize_diff_clasifica():
    changes = {
        "a": {"op": "upsert", "payload": {"name": "UNO"}},   # modified
        "b": {"op": "delete"},                                # removed
        "c": {"op": "upsert", "payload": {"name": "tres"}},   # added
    }
    assert summarize_diff(PUB, changes) == {"added": ["c"], "modified": ["a"], "removed": ["b"]}


# ── Doc 100 (P1/P2): llaves de primer nivel reservadas ─────────────────────

import pytest  # noqa: E402

from app.core.versioning.overlay import plain, reserved_key  # noqa: E402


@pytest.mark.parametrize("key", ["layout.evil", "udpValues.x", "a.b.c", "$where", "$set", "_id"])
def test_llaves_que_el_set_del_publish_lee_como_otra_cosa(key):
    """`a.b` es un camino (escribe DENTRO de `a`), `$x` un operador de la base
    y `_id` la identidad del registro."""
    assert reserved_key(key)


@pytest.mark.parametrize("key", ["layout", "routes", "udpValues", "id", "name", "a$b", "x_id", "_idx", ""])
def test_llaves_normales_no_son_reservadas(key):
    assert not reserved_key(key)


def test_plain_quita_solo_las_reservadas_y_no_copia_si_no_hay():
    doc = {"id": "a", "name": "n"}
    assert plain(doc) is doc
    dirty = {"id": "a", "layout": {"t1": {"x": 1, "y": 2}}, "layout.evil": {"x": "a"}, "$foo": 1, "_id": "zzz"}
    assert plain(dirty) == {"id": "a", "layout": {"t1": {"x": 1, "y": 2}}}
    assert "layout.evil" in dirty   # no muta la entrada


def test_overlay_no_devuelve_llaves_reservadas():
    """Un cambio grabado ANTES del arreglo con una llave reservada no puede
    volver al cliente: la mandaría de vuelta en su próximo guardado del doc
    completo, la entrada la rechazaría y nadie podría editar esa entidad."""
    pub = [{"id": "a", "name": "uno", "$foo": 1}]
    out = overlay(pub, {"b": {"op": "upsert", "payload": {"name": "dos", "layout.evil": {"x": "a"}, "_id": "zzz"}}})
    assert {e["id"]: e for e in out} == {"a": {"id": "a", "name": "uno"}, "b": {"id": "b", "name": "dos"}}
