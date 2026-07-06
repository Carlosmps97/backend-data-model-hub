"""Auditoría append-only (`audit_log`) — logs de entrada + acciones por usuario
para métricas de adopción (R12).

`audit(...)` es **best-effort**: nunca hace fallar la request que la invoca (un
fallo al escribir el log no debe tumbar una operación de negocio). Se llama
desde los services en el punto donde ocurre la acción.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.core.db.client import get_db
from app.core.logging import get_logger

COLL = "audit_log"
log = get_logger("app.audit")


async def audit(actor: str, action: str, *, target: str | None = None,
                target_type: str | None = None, meta: dict | None = None) -> None:
    """Registra `{at, actor, action, target?, targetType?, meta?}`. No levanta."""
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "actor": actor,
        "action": action,
    }
    if target is not None:
        entry["target"] = target
    if target_type is not None:
        entry["targetType"] = target_type
    if meta:
        entry["meta"] = meta
    try:
        db = await get_db()
        await db[COLL].insert_one(entry)
    except Exception:  # noqa: BLE001 — la auditoría nunca rompe el flujo
        log.warning("audit write failed", extra={"action": action, "actor": actor})
