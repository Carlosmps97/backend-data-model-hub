"""Lista fija de usuarios simulados (modo `local`).

Espeja el switch de actor del UI (`X-Dev-User`) y se usa para asignar revisores
en el flujo de aprobación. El `id` es el username (consistente con
`app/core/identity`: en local el `Principal.username` == `X-Dev-User`, p. ej.
"ana" → email "ana@local"). En Databricks real la identidad llega por headers
OBO; esta lista es sólo para la simulación local.
"""
from __future__ import annotations


def _initials(name: str) -> str:
    parts = [p for p in name.split() if p]
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


# id == username (clave usada por X-Dev-User / Principal.username).
_SIM_USERS: list[dict[str, str]] = [
    {"id": "ana", "name": "Ana Gomez"},
    {"id": "beto", "name": "Beto Diaz"},
    {"id": "carla", "name": "Carla Ruiz"},
    {"id": "qa", "name": "QA Tester"},
    {"id": "mr", "name": "Model Reviewer"},
]

SIM_USERS: list[dict[str, str]] = [
    {**u, "initials": _initials(u["name"])} for u in _SIM_USERS
]


def list_users() -> list[dict[str, str]]:
    """Devuelve la lista fija de usuarios simulados (`{id, name, initials}`)."""
    return [dict(u) for u in SIM_USERS]
