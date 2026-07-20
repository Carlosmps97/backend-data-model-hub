"""Smoke: el router de `summary` queda registrado en la app."""
from __future__ import annotations


def test_summary_route_registered(client):
    paths = {r.path for r in client.app.routes}
    assert "/api/summary" in paths
