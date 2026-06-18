"""Shim de compatibilidad — la app vive ahora en `app/main.py`.

Mantiene `uvicorn api.main:app` funcionando (comandos antiguos / Docker viejo).
Preferí el entrypoint nuevo: `uvicorn app.main:app`.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Asegura que la raíz del repo esté en el path (cuando se corre como api.main).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.main import app  # noqa: E402,F401

__all__ = ["app"]
