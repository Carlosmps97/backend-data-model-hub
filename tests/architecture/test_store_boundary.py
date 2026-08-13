"""El store (Lakebase, superficie estilo pymongo) sólo puede tocarse desde los `repository.py`.

Mantiene la persistencia swappable: services y schemas permanecen agnósticos al
store, así cambiar el destino sólo reescribe los repositories. El guard incluye
`motor`/`pymongo` para que ninguna capa fuera de repository importe el driver.
"""
from __future__ import annotations

from pathlib import Path

FEATURES = Path(__file__).resolve().parents[2] / "app" / "features"
FORBIDDEN = ("import motor", "from motor", "import pymongo", "from pymongo", "get_db")


def test_services_and_schemas_do_not_touch_the_store():
    offenders: list[str] = []
    for layer in ("service.py", "schemas.py"):
        for path in FEATURES.glob(f"*/{layer}"):
            text = path.read_text(encoding="utf-8")
            for needle in FORBIDDEN:
                if needle in text:
                    offenders.append(f"{path}: contiene '{needle}'")
    assert not offenders, "Fuga del store fuera de repository.py:\n" + "\n".join(offenders)
