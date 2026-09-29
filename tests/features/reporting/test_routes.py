"""Smoke: los routers de `reporting` quedan registrados en la app."""
from __future__ import annotations


def test_reporting_routes_registered(client):
    paths = {r.path for r in client.app.routes}
    assert "/api/reporting/tables" in paths
    assert "/api/reporting/columns" in paths
    assert "/api/reporting/filters" in paths      # doc 70 §4.1
    assert "/api/reporting/tables/count" in paths  # doc 92 D4
    assert "/api/reporting/columns/query" in paths   # doc 102: lotes por POST
    assert "/api/reporting/views/query" in paths
