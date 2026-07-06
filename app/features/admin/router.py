"""Endpoints del módulo Admin (pantalla 14). TODOS exigen `admin.manage` —
solo Administradores ven/usan el módulo.

  GET    /api/admin/users              GET  /api/admin/roles
  POST   /api/admin/users              POST /api/admin/roles/{key} (upsert)
  PUT    /api/admin/users/{username}   DELETE /api/admin/roles/{key}
  DELETE /api/admin/users/{username}   GET  /api/admin/permissions (catálogo)
  GET    /api/admin/audit              (lectura del log de auditoría)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.api.envelope import ok
from app.features.auth.deps import require_permission

from . import service
from .service import AdminGuardError
from .schemas import RoleBody, UserCreate, UserUpdate

router = APIRouter(prefix="/api/admin", tags=["admin"])
_admin = require_permission("admin.manage")


def _guard(exc: AdminGuardError) -> HTTPException:
    """Guard de negocio (dejar sin admin / rol con usuarios) → 400 legible."""
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


# ── Users ─────────────────────────────────────────────────────────────────


@router.get("/users")
async def list_users(user: dict = Depends(_admin)):
    return ok(await service.list_users())


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreate, user: dict = Depends(_admin)):
    return ok(await service.create_user(user["username"], body))


@router.put("/users/{username}")
async def update_user(username: str, body: UserUpdate, user: dict = Depends(_admin)):
    try:
        res = await service.update_user(user["username"], username, body)
    except AdminGuardError as exc:
        raise _guard(exc) from exc
    if res is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    return ok(res)


@router.delete("/users/{username}")
async def delete_user(username: str, user: dict = Depends(_admin)):
    try:
        deleted = await service.delete_user(user["username"], username)
    except AdminGuardError as exc:
        raise _guard(exc) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    return ok({"id": username})


# ── Roles + matriz ─────────────────────────────────────────────────────────


@router.get("/roles")
async def list_roles(user: dict = Depends(_admin)):
    return ok(await service.list_roles())


@router.put("/roles/{key}")
async def upsert_role(key: str, body: RoleBody, user: dict = Depends(_admin)):
    try:
        return ok(await service.upsert_role(user["username"], key, body))
    except AdminGuardError as exc:
        raise _guard(exc) from exc


@router.delete("/roles/{key}")
async def delete_role(key: str, user: dict = Depends(_admin)):
    try:
        deleted = await service.delete_role(user["username"], key)
    except AdminGuardError as exc:
        raise _guard(exc) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="Rol no encontrado.")
    return ok({"id": key})


@router.get("/permissions")
async def permissions(user: dict = Depends(_admin)):
    """Catálogo de permisos (columnas/filas de la matriz)."""
    return ok(service.permissions_catalog())


# ── Auditoría (lectura) ─────────────────────────────────────────────────────


@router.get("/audit")
async def audit_log(
    user: dict = Depends(_admin),
    limit: int = Query(default=200, ge=1, le=1000),
):
    from app.core.db.client import get_db
    db = await get_db()
    docs = await db["audit_log"].find({}).sort("at", -1).limit(limit).to_list(None)
    for d in docs:
        d["id"] = str(d.pop("_id", ""))
    return ok(docs)
