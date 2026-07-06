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


async def main() -> None:
    from app.core.db.client import connect, disconnect, get_db
    from app.core.security import hash_password
    from scripts.seed_modeler import build_roles

    await connect()
    db = await get_db()
    now = datetime.now(timezone.utc).isoformat()

    # 1) Asegurar roles (el admin necesita el doc de rol para resolver permisos).
    for r in build_roles():
        await db["roles"].update_one({"_id": r["_id"]}, {"$set": r}, upsert=True)

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
