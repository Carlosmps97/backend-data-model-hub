"""Async CRUD for the `users` collection (Motor / Cosmos DB).

Mirrors `web-data-model-hub/src/server/db/users.ts`.

Notes:
- `listUsers` sorts in Python (not via .sort()) — Cosmos DB RU rejects
  .sort() on fields without an explicit index.
- `deleteUser` is a hard delete (same as TypeScript). Use `isActive=False`
  for temporary lock-out.
- `markLoginSuccess` is best-effort: swallows all exceptions so a login
  never fails because of an audit write.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pymongo import ReturnDocument

from src.db.motor_client import get_db
from src.db.db_models import UserDoc


# ── Helpers ────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_user(doc: dict) -> UserDoc:
    doc = dict(doc)
    doc["id"] = str(doc.pop("_id"))
    return UserDoc.model_validate(doc)


# ── CRUD ───────────────────────────────────────────────────────────────────

async def list_users() -> list[UserDoc]:
    db = await get_db()
    docs = await db["users"].find({}).to_list(None)
    users = [_to_user(d) for d in docs]
    users.sort(key=lambda u: u.createdAt)
    return users


async def get_user_by_id(user_id: str) -> UserDoc | None:
    db = await get_db()
    doc = await db["users"].find_one({"_id": user_id})
    return _to_user(doc) if doc else None


async def get_user_by_username(username: str) -> UserDoc | None:
    db = await get_db()
    doc = await db["users"].find_one({"username": username})
    return _to_user(doc) if doc else None


async def create_user(user: UserDoc) -> UserDoc:
    db = await get_db()
    data = user.model_dump(exclude={"id"})
    await db["users"].insert_one({"_id": user.id, **data})
    return user


async def update_user(
    user_id: str,
    updates: dict,
) -> UserDoc | None:
    db = await get_db()
    updates.pop("id", None)
    result = await db["users"].find_one_and_update(
        {"_id": user_id},
        {"$set": updates},
        return_document=ReturnDocument.AFTER,
    )
    return _to_user(result) if result else None


async def delete_user(user_id: str) -> bool:
    """Hard-delete. Use `update_user(id, {"isActive": False})` for lock-out."""
    db = await get_db()
    result = await db["users"].delete_one({"_id": user_id})
    return result.deleted_count > 0


async def mark_login_success(user_id: str) -> None:
    """Stamp `lastLoginAt`. Best-effort — never raises."""
    try:
        db = await get_db()
        await db["users"].update_one(
            {"_id": user_id},
            {"$set": {"lastLoginAt": _now()}},
        )
    except Exception:  # noqa: BLE001
        pass
