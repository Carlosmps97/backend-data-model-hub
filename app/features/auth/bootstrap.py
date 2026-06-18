"""Bootstrap del admin por defecto (admin / dogadmin2019).

Idempotente, solo en el primer arranque: crea el admin si no existe ninguno.
Espeja `web-data-model-hub/src/server/auth/bootstrap.ts`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.core.security.passwords import hash_password
from app.features.users import UserDoc, create_user, get_user_by_username

_bootstrap_done = False


async def ensure_bootstrap_admin() -> None:
    """Crea admin/dogadmin2019 si no existe ningún admin. Idempotente, traga errores."""
    global _bootstrap_done
    if _bootstrap_done:
        return
    try:
        existing = await get_user_by_username("admin")
        if not existing:
            now = datetime.now(timezone.utc).isoformat()
            admin = UserDoc(
                id=str(uuid.uuid4()),
                username="admin",
                passwordHash=hash_password("dogadmin2019"),
                role="admin",
                isActive=True,
                permissions=[],
                createdAt=now,
                statusUpdatedAt=now,
            )
            await create_user(admin)
        _bootstrap_done = True
    except Exception:  # noqa: BLE001
        pass
