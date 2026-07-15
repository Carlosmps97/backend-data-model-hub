"""El Principal serializa la identidad del usuario en sesión."""
from __future__ import annotations

from app.core.identity import Principal


def test_principal_roundtrip():
    p = Principal(
        email="ana@corp.com",
        username="ana",
        display_name="Ana Gómez",
        source="databricks",
    )
    assert p.model_dump() == {
        "email": "ana@corp.com",
        "username": "ana",
        "display_name": "Ana Gómez",
        "source": "databricks",
    }
