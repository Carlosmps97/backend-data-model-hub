"""Doc 105 (A2-o6): la suite E2E (`scripts/e2e`) corre contra la app EN MEMORIA
— routers, services, repositorios y RBAC reales sobre la BD fake de los tests,
con login real por rol (bcrypt + JWT). Cada escenario termina sin checks
fallidos: la suite ya no le pega a rutas que no existen ni a escrituras
directas cerradas (el modelo se escribe por versiones; los estándares, por
Data Standards)."""
from __future__ import annotations

from datetime import datetime, timezone

import bcrypt
import pytest

from app.core.db import client as db_client
from app.main import app
from scripts.create_admin import build_roles
from scripts.e2e import harness as H
from scripts.e2e.scenarios import ALL
from tests.support.fakedb import FakeDb

# Defectos CONOCIDOS del producto que la suite destapa, por escenario (nombre
# exacto del check). Exactos: si se arreglan, este test falla hasta sacarlos de
# acá. Vacío desde el doc 105 (la re-carga idéntica del Excel ya es «unchanged»).
KNOWN_FAILURES: dict[str, set[str]] = {}


@pytest.fixture
def inprocess(monkeypatch):
    db = FakeDb()
    now = datetime.now(timezone.utc).isoformat()
    for role in build_roles():
        db.raw["roles"].insert_one(role)
    for key, (username, password) in H.ROLE_USER.items():
        db.raw["users"].insert_one({
            "_id": username, "email": f"{username}@local", "name": username.title(), "role": H.ROLE_MATRIX[key],
            "projectIds": [], "status": "active", "flgactive": True, "createdAt": now, "updatedAt": now,
            # rondas mínimas de bcrypt: el login verifica igual, en milisegundos
            "passwordHash": bcrypt.hashpw(password.encode(), bcrypt.gensalt(4)).decode()})
    monkeypatch.setattr(db_client, "_pg_db", db)
    monkeypatch.setattr(app.state, "db_connected", True, raising=False)
    monkeypatch.setattr(H, "_HTTP_FACTORY", None)
    H.use_app(app)
    yield db
    H.cleanup()          # en memoria sólo vacía el registro de ids


@pytest.mark.parametrize("name", list(ALL))
def test_escenario_e2e_en_memoria(inprocess, name):
    summary = ALL[name]().summary()
    failed = {c["name"]: c["detail"] for c in summary["checks"] if not c["ok"]}
    assert summary["total"] > 0
    assert set(failed) == KNOWN_FAILURES.get(name, set()), "\n".join(f"{k} — {v}" for k, v in failed.items())
