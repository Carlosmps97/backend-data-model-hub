"""Helpers HTTP del backend de plataforma.

Por ahora solo expone el sobre estándar `ok(...)`. La lógica de
ensamblado del modelo de datos (mapeo LLM → TableAPI, audit columns,
DDL, markdown) vive ahora en el servicio de agentes
(`app-agents-modeler/src/api/response_builder.py`).
"""

from __future__ import annotations

from typing import Any


def ok(data: Any = None) -> dict[str, Any]:
    """Envuelve datos en el sobre estándar: {success: true, data: ...}."""
    return {"success": True, "data": data}
