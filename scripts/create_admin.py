"""Crea/actualiza el usuario `admin` / `admin` (Administrador) SIN wipear nada.

Atajo de desarrollo para poder loguear rápido. Asegura además los 4 roles (para
que los permisos del admin resuelvan) — todo idempotente (upsert). No toca las
colecciones del modelo.

Run:  backend-data-model-hub/.venv/bin/python scripts/create_admin.py
"""
from __future__ import annotations

import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Consola en UTF-8 (Windows viene en cp1252 y estos prints llevan acentos).
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

# Matriz de permisos por rol (pantalla 14 + Data Standards). Editable luego
# desde Admin; acá solo se asegura que los 4 roles EXISTAN con su default.
_ROLE_GRANTS: dict[str, tuple[str, str, set[str]]] = {
    "administrador": ("Administrador", "Control total · gestiona usuarios", set()),  # set() → todos
    # doc 70 §12: el modelador puede PEDIR un rollback (crea un draft que pasa
    # por revisión — la seguridad está en el approve), comparar y ver detalles;
    # `versions.view_all` (ver drafts ajenos en el canvas) queda solo en admin.
    "modelador": ("Modelador", "Crea y edita tablas · envía a revisión",
                  {"model.view", "model.edit", "export", "rollback"}),
    "revisor": ("Revisor", "Aprueba o rechaza solicitudes",
                {"model.view", "review.decide", "publish", "rollback", "export"}),
    "lector": ("Lector", "Solo lectura · exporta metadata", {"model.view", "export"}),
}


def build_roles() -> list[dict]:
    from app.features.auth.models import PERMISSIONS
    now = datetime.now(timezone.utc).isoformat()
    out = []
    for key, (name, desc, grants) in _ROLE_GRANTS.items():
        allowed = set(PERMISSIONS) if not grants else grants
        out.append({
            "_id": key, "name": name, "description": desc,
            "permissions": {p: (p in allowed) for p in PERMISSIONS},
            "flgactive": True, "createdAt": now, "updatedAt": now,
        })
    return out


async def main() -> None:
    from app.core.db.client import connect, disconnect, get_db
    from app.core.security import hash_password

    await connect()
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()

    # 1) Asegurar roles (el admin necesita el doc de rol para resolver permisos).
    #    $setOnInsert: un rol EXISTENTE no se pisa (la matriz editada en Admin manda).
    for r in build_roles():
        await db["roles"].update_one(
            {"_id": r["_id"]},
            {"$setOnInsert": {k: v for k, v in r.items() if k != "_id"}},
            upsert=True,
        )

    # 2) Upsert del usuario admin/admin.
    await db["users"].update_one(
        {"_id": "admin"},
        {"$set": {
            "email": "admin@empresa.com", "name": "Admin", "role": "administrador",
            "projectIds": [], "status": "active", "initials": "AD",
            "passwordHash": hash_password("admin"), "flgactive": True, "updatedAt": now,
        }, "$setOnInsert": {"createdAt": now}},
        upsert=True,
    )
    print("OK · usuario 'admin' / 'admin' (role=administrador) listo. Roles asegurados.")
    await disconnect()


if __name__ == "__main__":
    asyncio.run(main())
