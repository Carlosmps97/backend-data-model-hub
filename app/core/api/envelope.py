"""Sobre de respuesta estándar del backend de plataforma."""

from __future__ import annotations

from typing import Any


def ok(data: Any = None) -> dict[str, Any]:
    """Envuelve datos en el sobre estándar: {success: true, data: ...}."""
    return {"success": True, "data": data}
