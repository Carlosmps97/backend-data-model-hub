"""`merge_layout` actualiza posiciones por tabla sin perder las demás."""
from __future__ import annotations

from app.features.projects.service import merge_layout


def test_merge_layout_actualiza_y_conserva():
    base = {"t1": {"x": 0, "y": 0}, "t2": {"x": 5, "y": 5}}
    out = merge_layout(base, {"t1": {"x": 10, "y": 20}})
    assert out == {"t1": {"x": 10, "y": 20}, "t2": {"x": 5, "y": 5}}


def test_merge_layout_agrega_nueva():
    out = merge_layout({}, {"t9": {"x": 1, "y": 2}})
    assert out == {"t9": {"x": 1, "y": 2}}
