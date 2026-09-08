"""Crea/actualiza el usuario local `admin` y siembra la whitelist SSO de
Modeladores, SIN wipear nada.

Es el paso que el one-shot de Databricks corre para dejar la plataforma con
acceso desde el arranque: asegura los 4 roles de caja, la cuenta local `admin`
(contraseña `ADMIN_PASSWORD`) y las entradas de whitelist SSO de los correos
declarados en `MODELER_EMAILS` (rol `modelador`). También sirve como atajo de
desarrollo. Todo idempotente (upsert). No toca las colecciones del modelo.

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

# ── Cuenta local de administración (login usuario/contraseña, sin SSO) ────────
ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "BCP$4M4Y2026"

# ── Whitelist SSO: correos que entran con rol Modelador (key `modelador`) ──────
# Son entradas de whitelist (doc 38): `_id == correo` en minúsculas y SIN
# contraseña — el SSO de Databricks los deja entrar y rellena nombre/iniciales
# en el primer login (SCIM → hint → local-part). El admin puede editarlas luego.
MODELER_EMAILS: tuple[str, ...] = (
    "angellachavez@bcp.com.pe",
    "edsonmio@bcp.com.pe",
    "henryyancul@bcp.com.pe",
    "jaimesanchez@bcp.com.pe",
    "jessicavega@bcp.com.pe",
    "jesusmsanchez@bcp.com.pe",
    "jferro@bcp.com.pe",
    "jhonnyimillones@bcp.com.pe",
    "juanapoon@bcp.com.pe",
    "katterinemaguina@bcp.com.pe",
    "kevinavalos@bcp.com.pe",
    "paulisla@bcp.com.pe",
    "ricardohinostroza@bcp.com.pe",
    "roggerpuse@bcp.com.pe",
)

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


def build_admin_doc() -> dict:
    """Doc de la cuenta local `admin` con su hash bcrypt de `ADMIN_PASSWORD`.
    Los campos (sin `_id`) van a `$set`; `main()` agrega los timestamps."""
    from app.core.security import hash_password
    return {
        "_id": ADMIN_USERNAME,
        "email": "admin@empresa.com", "name": "Admin", "role": "administrador",
        "projectIds": [], "status": "active", "initials": "AD",
        "passwordHash": hash_password(ADMIN_PASSWORD), "flgactive": True,
    }


def build_modeler_users() -> list[dict]:
    """Entradas de whitelist SSO para `MODELER_EMAILS` (rol `modelador`). Sin
    `passwordHash` (entran por SSO); `projectIds=[]` = todos los proyectos hasta
    que un admin las acote. `name`/`initials` los pone el primer login SSO."""
    return [
        {
            "_id": email.lower(), "email": email.lower(), "role": "modelador",
            "projectIds": [], "status": "active", "flgactive": True,
        }
        for email in MODELER_EMAILS
    ]


async def main() -> None:
    from app.core.db.client import connect, disconnect, get_db

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

    # 2) Upsert de la cuenta local `admin` (contraseña ADMIN_PASSWORD). $set
    #    re-estampa la clave en cada corrida del one-shot.
    admin = build_admin_doc()
    await db["users"].update_one(
        {"_id": admin["_id"]},
        {"$set": {**{k: v for k, v in admin.items() if k != "_id"}, "updatedAt": now},
         "$setOnInsert": {"createdAt": now}},
        upsert=True,
    )

    # 3) Whitelist SSO de Modeladores (lista declarada). `role`/`status` se
    #    re-afirman; `name`/`initials` quedan en $setOnInsert para no pisar lo
    #    que el SSO/Admin ya hubiera puesto en una corrida previa.
    for u in build_modeler_users():
        await db["users"].update_one(
            {"_id": u["_id"]},
            {"$set": {**{k: v for k, v in u.items() if k != "_id"}, "updatedAt": now},
             "$setOnInsert": {"createdAt": now, "name": "", "initials": None}},
            upsert=True,
        )

    print(f"OK · cuenta local '{ADMIN_USERNAME}' (role=administrador) lista · "
          f"{len(MODELER_EMAILS)} Modeladores en whitelist SSO · roles asegurados.")
    await disconnect()


if __name__ == "__main__":
    asyncio.run(main())
