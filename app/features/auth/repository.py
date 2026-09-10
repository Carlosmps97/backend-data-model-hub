"""CRUD async de `users` + `roles` (auth propia / RBAC).

`users._id == username`; `roles._id == role key`. `_to_user` NUNCA incluye
`passwordHash` (el hash solo lo lee `get_user_with_hash` para el login)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pymongo import ReturnDocument

from app.core.db.client import get_db
from app.core.logging import get_logger

from .models import RoleDoc, UserDoc

USERS = "users"
ROLES = "roles"
log = get_logger("app.auth")


def _safe(fn, doc: dict):
    """Aplica el conversor a un doc; si un doc legacy/corrupto no valida, lo
    saltea (log) en vez de tumbar TODO el listado. Devuelve None al saltar."""
    try:
        return fn(doc)
    except Exception:  # noqa: BLE001
        log.warning("skipping unparseable doc", extra={"id": str(doc.get("_id"))})
        return None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_user(doc: dict, *, with_hash: bool = False) -> dict:
    """Doc Mongo → dict de UserDoc. Sin `passwordHash` salvo `with_hash`.
    `hasPassword` se DERIVA acá (no se persiste): el front lo usa para mostrar
    el reset de contraseña solo en cuentas locales (doc 38)."""
    d = dict(doc)
    d["id"] = str(d.pop("_id"))
    d["hasPassword"] = bool(d.get("passwordHash"))
    for k in ("flgactive", "deletedAt", "updatedAt", "createdAt"):
        d.pop(k, None)
    out = UserDoc.model_validate(d).model_dump()
    if not with_hash:
        out.pop("passwordHash", None)
    return out


def _to_role(doc: dict) -> dict:
    d = dict(doc)
    d["id"] = str(d.pop("_id"))
    for k in ("flgactive", "deletedAt", "updatedAt", "createdAt"):
        d.pop(k, None)
    return RoleDoc.model_validate(d).model_dump()


# ── Users ─────────────────────────────────────────────────────────────────


async def list_users() -> list[dict]:
    db = await get_db()
    docs = await db[USERS].find({"flgactive": {"$ne": False}}).to_list(None)
    docs.sort(key=lambda d: (d.get("name") or "").lower())
    return [u for d in docs if (u := _safe(_to_user, d)) is not None]


async def get_user(username: str, *, with_hash: bool = False) -> dict | None:
    db = await get_db()
    doc = await db[USERS].find_one({"_id": username, "flgactive": {"$ne": False}})
    # `_safe`: un doc legacy con un campo de tipo inválido no debe reventar
    # login/resolución (devuelve None → se trata como usuario inexistente).
    return _safe(lambda d: _to_user(d, with_hash=with_hash), doc) if doc else None


async def upsert_user(username: str, fields: dict) -> dict:
    """Crea/actualiza por `_id`. `fields` ya viene saneado por el service
    (incluye passwordHash cuando corresponde). Devuelve el user SIN hash."""
    db = await get_db()
    # `hasPassword` es derivado de lectura (_to_user): jamás se persiste.
    body = {k: v for k, v in fields.items() if k not in ("id", "hasPassword")}
    doc = await db[USERS].find_one_and_update(
        {"_id": username},
        {"$set": {**body, "flgactive": True, "updatedAt": _now()},
         "$setOnInsert": {"createdAt": _now()}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return _to_user(doc)


# ── Lockout de login (campos internos, NO pasan por UserDoc/_to_user) ────────
async def get_login_record(username: str) -> dict | None:
    """Datos CRUDOS para el login: hash + estado + contadores de lockout. No pasa
    por UserDoc (los campos de lockout son internos, no se exponen al frontend)."""
    db = await get_db()
    return await db[USERS].find_one(
        {"_id": username, "flgactive": {"$ne": False}},
        {"passwordHash": 1, "status": 1, "failedAttempts": 1, "lockedUntil": 1},
    )


async def register_failed_login(username: str, threshold: int, lock_minutes: int) -> None:
    """$inc atómico de failedAttempts; si alcanza el umbral, bloquea (lockedUntil)."""
    db = await get_db()
    doc = await db[USERS].find_one_and_update(
        {"_id": username, "flgactive": {"$ne": False}},
        {"$inc": {"failedAttempts": 1}, "$set": {"updatedAt": _now()}},
        projection={"failedAttempts": 1},
        return_document=ReturnDocument.AFTER,
    )
    if doc and doc.get("failedAttempts", 0) >= threshold:
        until = (datetime.now(timezone.utc) + timedelta(minutes=lock_minutes)).isoformat()
        await db[USERS].update_one({"_id": username}, {"$set": {"lockedUntil": until}})


async def clear_failed_login(username: str) -> None:
    """Éxito de login → resetea el contador y quita el bloqueo."""
    db = await get_db()
    await db[USERS].update_one(
        {"_id": username, "flgactive": {"$ne": False}},
        {"$set": {"failedAttempts": 0}, "$unset": {"lockedUntil": ""}},
    )


async def set_password_hash(username: str, password_hash: str) -> bool:
    db = await get_db()
    res = await db[USERS].update_one(
        {"_id": username, "flgactive": {"$ne": False}},
        {"$set": {"passwordHash": password_hash, "updatedAt": _now()}},
    )
    return res.modified_count > 0


async def delete_user(username: str) -> bool:
    db = await get_db()
    # Filtra flgactive (doc 82): sin él, un doble-delete o un username ya
    # borrado re-tocaba `deletedAt`, devolvía True y se auditaba otra vez
    # (mismo bug que tuvo delete_subject_area); ahora → False → 404.
    res = await db[USERS].update_one(
        {"_id": username, "flgactive": {"$ne": False}},
        {"$set": {"flgactive": False, "deletedAt": _now()}},
    )
    return res.modified_count > 0


# ── Roles ─────────────────────────────────────────────────────────────────


async def list_roles() -> list[dict]:
    db = await get_db()
    docs = await db[ROLES].find({"flgactive": {"$ne": False}}).to_list(None)
    docs.sort(key=lambda d: (d.get("name") or "").lower())
    return [r for d in docs if (r := _safe(_to_role, d)) is not None]


async def get_role(key: str) -> dict | None:
    db = await get_db()
    doc = await db[ROLES].find_one({"_id": key, "flgactive": {"$ne": False}})
    return _safe(_to_role, doc) if doc else None


async def upsert_role(key: str, fields: dict) -> dict:
    db = await get_db()
    body = {k: v for k, v in fields.items() if k != "id"}
    doc = await db[ROLES].find_one_and_update(
        {"_id": key},
        {"$set": {**body, "flgactive": True, "updatedAt": _now()},
         "$setOnInsert": {"createdAt": _now()}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return _to_role(doc)


async def delete_role(key: str) -> bool:
    db = await get_db()
    res = await db[ROLES].update_one(
        {"_id": key}, {"$set": {"flgactive": False, "deletedAt": _now()}}
    )
    return res.modified_count > 0
