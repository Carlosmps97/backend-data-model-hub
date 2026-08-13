"""E2E — login SSO heredado de Databricks + whitelist de correos (doc 38).

Contra el backend EN VIVO (localhost:8000, REQUIRE_AUTH=false y sin
PROXY_SHARED_SECRET → el endpoint SSO acepta los headers de relay directo,
como los mandaría el server.mjs del front). Ejercita:

  admin whitelistea un correo → SSO entra con el rol asignado → /me con
  permisos → correo NO whitelisteado 403 → disabled 403 → removido 403.

El gate del secreto compartido (401/503) se cubre en pytest
(tests/features/auth/test_sso_login.py) — acá el server corre sin secreto.

Uso:  python scripts/e2e/e2e_sso.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import httpx  # noqa: E402

from harness import BASE, TAG, Client, Resp, Suite, _track, cleanup  # noqa: E402


def main() -> int:
    s = Suite("sso_whitelist")
    email = f"{TAG.lower()}.sso@e2e.local"
    anon = httpx.Client(base_url=BASE, timeout=30.0)

    try:
        admin = Client("admin")

        # 1) Alta en la whitelist: correo + rol, SIN password.
        r = admin.post("/api/admin/users", {"username": email.upper(), "role": "lector"})
        s.check("alta whitelist (correo+rol, sin password)", r.status == 201, f"status={r.status}")
        if r.status == 201:
            _track("users", r.data["id"])
            s.eq("username normalizado a lowercase", r.data["id"], email)
            s.eq("email autocompletado", r.data.get("email"), email)
            s.eq("entrada SSO sin password", r.data.get("hasPassword"), False)

        # 2) Login SSO con el relay de identidad (como lo manda server.mjs).
        r = Resp(anon.post("/api/auth/sso/login", headers={
            "x-dmh-sso-email": email.upper(),          # el backend normaliza
            "x-dmh-sso-username": email,
            "x-dmh-sso-user-id": "e2e-42",
        }))
        s.check("login SSO whitelisteado entra", r.status == 200, f"status={r.status}")
        token = (r.data or {}).get("token")
        user = (r.data or {}).get("user") or {}
        s.eq("username de la sesión = correo", user.get("username"), email)
        s.eq("rol asignado por la whitelist", user.get("role"), "lector")
        s.check("nombre auto-resuelto (SCIM/derivado)", bool((user.get("name") or "").strip()),
                f"name={user.get('name')!r}")

        # 3) /me con el token emitido → permisos efectivos del rol.
        r = Resp(anon.get("/api/auth/me", headers={"X-Session-Token": token or ""}))
        s.check("/me con el token SSO", r.status == 200, f"status={r.status}")
        perms = (r.data or {}).get("permissions") or {}
        s.eq("lector puede ver el modelo", perms.get("model.view"), True)
        s.eq("lector NO administra", perms.get("admin.manage"), False)

        # 4) Correo NO whitelisteado → 403 con mensaje accionable.
        r = Resp(anon.post("/api/auth/sso/login",
                           headers={"x-dmh-sso-email": f"nadie.{TAG.lower()}@e2e.local"}))
        s.check("correo sin whitelist → 403", r.status == 403, f"status={r.status}")
        s.check("403 con mensaje accionable",
                "assign your email" in str((r.raw or {}).get("detail", "")),
                str(r.raw)[:120])

        # 5) Deshabilitar la entrada → el SSO deja de entrar (sin borrarla).
        r = admin.put(f"/api/admin/users/{email}", {"status": "disabled"})
        s.check("disable de la entrada", r.status == 200, f"status={r.status}")
        r = Resp(anon.post("/api/auth/sso/login", headers={"x-dmh-sso-email": email}))
        s.check("disabled → 403", r.status == 403, f"status={r.status}")

        # 6) Quitar la entrada (revocar acceso) → 403.
        r = admin.delete(f"/api/admin/users/{email}")
        s.check("remoción de la whitelist", r.status == 200, f"status={r.status}")
        r = Resp(anon.post("/api/auth/sso/login", headers={"x-dmh-sso-email": email}))
        s.check("removido → 403", r.status == 403, f"status={r.status}")

    finally:
        anon.close()
        cleanup()

    result = s.done()
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
