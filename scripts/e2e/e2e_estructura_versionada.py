"""E2E de la 'estructura versionada' (doc 16).

Flujo: carpeta + canvas + tabla + columna registrados EN el draft, invisibles en
producción y visibles en el efectivo hasta aprobar.

Doc 105: el flujo vive en `scenarios.s26_estructura_versionada` — proyecto propio, el modelo
siempre por versiones, verificado en memoria por
`tests/scripts/test_e2e_inprocess.py` —; este archivo queda como atajo del
comando de siempre contra el backend vivo (`E2E_BASE`, default localhost:8000).

Uso:  python scripts/e2e/e2e_estructura_versionada.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.e2e.run_e2e import run_one  # noqa: E402

if __name__ == "__main__":
    sys.exit(1 if run_one("s26_estructura_versionada").get("failed") else 0)
