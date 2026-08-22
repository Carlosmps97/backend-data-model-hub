"""Runner de escenarios E2E. Uso:
    python -m scripts.e2e.run_e2e <scenario>     # uno (imprime ===E2E_RESULT=== JSON)
    python -m scripts.e2e.run_e2e all            # todos, serial
"""
from __future__ import annotations

import sys
import traceback

from scripts.e2e import harness as H
from scripts.e2e.scenarios import ALL


def run_one(name: str) -> dict:
    fn = ALL[name]
    try:
        suite = fn()
        return suite.done()
    except Exception as e:  # noqa: BLE001
        print(f"[{name}] EXCEPCIÓN: {e}", file=sys.stderr)
        traceback.print_exc()
        return {"scenario": name, "error": str(e), "passed": 0, "failed": 1, "total": 1}
    finally:
        H.cleanup()


def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which == "all":
        results = [run_one(n) for n in ALL]
        tot = sum(r.get("total", 0) for r in results)
        fail = sum(r.get("failed", 0) for r in results)
        print(f"\n===E2E_TOTAL=== passed={tot - fail}/{tot} scenarios={len(results)}")
        sys.exit(1 if fail else 0)
    if which not in ALL:
        print(f"desconocido: {which}. Disponibles: {', '.join(ALL)}")
        sys.exit(2)
    r = run_one(which)
    sys.exit(1 if r.get("failed") else 0)


if __name__ == "__main__":
    main()
