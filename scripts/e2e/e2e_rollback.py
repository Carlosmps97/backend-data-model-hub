"""E2E del rollback de versiones (semántica doc 27).

Flujo: la versión vigente no se revierte (409); publicar un cambio → restaurar
la versión previa (draft inverso) → aprobar → la entidad vuelve a su estado.

Doc 105: el flujo vive en `scenarios.s24_rollback` — proyecto propio, el modelo
siempre por versiones, verificado en memoria por
`tests/scripts/test_e2e_inprocess.py` —; este archivo queda como atajo del
comando de siempre contra el backend vivo (`E2E_BASE`, default localhost:8000).

Uso:  python scripts/e2e/e2e_rollback.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.e2e.run_e2e import run_one  # noqa: E402

if __name__ == "__main__":
    sys.exit(1 if run_one("s24_rollback").get("failed") else 0)
