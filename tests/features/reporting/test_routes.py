"""Smoke: los routers de `reporting` quedan registrados en la app."""
from __future__ import annotations


def test_reporting_routes_registered(client):
    paths = {r.path for r in client.app.routes}
    assert "/api/reporting/tables" in paths
    assert "/api/reporting/columns" in paths
    assert "/api/reporting/filters" in paths      # doc 70 §4.1
