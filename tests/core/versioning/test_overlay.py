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
