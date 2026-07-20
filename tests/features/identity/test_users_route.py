"""GET /api/users devuelve la lista fija de usuarios simulados (sin DB)."""
from __future__ import annotations

from app.features.identity.users import list_users


def test_users_route(client):
    resp = client.get("/api/users")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    ids = [u["id"] for u in body["data"]]
    assert ids == ["ana", "beto", "carla", "qa", "mr"]
    for u in body["data"]:
        assert set(u.keys()) == {"id", "name", "initials"}
        assert u["initials"]


def test_users_initials_consistent_with_identity():
    by_id = {u["id"]: u for u in list_users()}
    # id == username del Principal local (X-Dev-User="ana" → username "ana").
    assert by_id["ana"]["initials"] == "AG"
    assert by_id["mr"]["initials"] == "MR"
