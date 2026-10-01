"""Doc 104 — reglas PURAS de administración de versiones: quién puede
transferir/eliminar un draft, la entrada de transferencia y cómo se refleja
en la fila de versión y en el historial («started by»)."""
from __future__ import annotations

import pytest

from app.features.changesets import access, service

DRAFT = {"id": "d1", "status": "draft", "owner": "ana", "reviewers": ["beto"]}
EDIT = {"model.edit": True}
ADMIN = {"admin.manage": True}


@pytest.mark.parametrize("username,perms,expected", [
    ("ana", EDIT, True),                          # owner que puede editar
    ("ana", {"model.view": True}, False),         # owner cuyo rol ya no edita
    ("carla", EDIT, False),                       # modelador ajeno
    ("beto", {"review.decide": True}, False),     # revisor asignado: revisa, no administra
    ("root", ADMIN, True),                        # administrador: cualquier versión
    (None, ADMIN, False),                         # sin sesión
    ("ana", None, False),                         # sin permisos resueltos
])
def test_can_manage(username, perms, expected):
    assert access.can_manage(DRAFT, username, perms) is expected


def test_can_manage_version_inexistente():
    assert access.can_manage(None, "root", ADMIN) is False


def test_transfer_entry_normaliza_la_nota():
    e = service.transfer_entry("ana", "carla", "admin", "  vacaciones  ")
    assert (e["from"], e["to"], e["by"], e["note"]) == ("ana", "carla", "admin", "vacaciones")
    assert e["at"]
    assert "note" not in service.transfer_entry("ana", "carla", "ana", "   ")
    assert "note" not in service.transfer_entry("ana", "carla", "ana", None)


def test_version_row_proyecta_las_transferencias():
    t = {"from": "ana", "to": "carla", "by": "ana", "at": "2026-09-29T10:00:00+00:00"}
    row = service.version_row({**DRAFT, "owner": "carla", "projectId": "p1", "transfers": [t]})
    assert row["owner"] == "carla" and row["transfers"] == [t]
    assert service.version_row({**DRAFT, "projectId": "p1"})["transfers"] == []


def _hdr(cs_id: str, **extra) -> dict:
    return {"id": cs_id, "status": "approved", "appliedAt": "2026-09-29T12:00:00+00:00",
            "owner": "carla", "versionLabel": "v5", "title": "t", **extra}


def test_historial_marca_quien_inicio_una_version_transferida():
    changes = [{"csId": "c1", "op": "upsert", "payload": {"physicalName": "X"}, "before": None,
                "beforeAt": "2026-09-29T11:00:00+00:00", "at": "2026-09-29T09:00:00+00:00"}]
    transfers = [{"from": "ana", "to": "beto", "by": "ana", "at": "a"},
                 {"from": "beto", "to": "carla", "by": "admin", "at": "b"}]
    [ev] = service.history_events(changes, {"c1": _hdr("c1", transfers=transfers)})
    assert ev["userId"] == "carla"            # autor = quien la publicó (dueño final)
    assert ev["startedById"] == "ana"         # quien la empezó (primera transferencia)
    [plain] = service.history_events(changes, {"c1": _hdr("c1")})
    assert plain["userId"] == "carla" and plain["startedById"] is None
