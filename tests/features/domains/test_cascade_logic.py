"""La cascada apunta sólo a columnas del dominio SIN override manual."""
from __future__ import annotations

from app.features.domains.service import cascade_filter


def test_cascade_filter_targets_domain_without_override():
    assert cascade_filter("pd-monto") == {
        "parentDomainId": "pd-monto",
        "typeOverridden": {"$ne": True},
        "flgactive": {"$ne": False},   # no re-tipar columnas soft-deleted
    }
