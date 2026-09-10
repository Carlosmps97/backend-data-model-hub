"""Historial de auditoría por entidad (doc 51) — derivado en LECTURA del
ledger de changesets: `history_events` (pura), `entity_history` (orquestación,
repository mockeado) y el carry-forward de `origin` en el repository."""
from __future__ import annotations

import asyncio

from app.features.changesets import repository, service
from app.features.changesets.schemas import ChangeBody

AT = "2026-08-10T10:00:00+00:00"


def _ch(cs_id: str, op: str = "upsert", before=None, before_at: str | None = "2026-08-11T09:00:00+00:00",
        payload: dict | None = None, origin: dict | None = None) -> dict:
    return {"id": f"{cs_id}::canonical_tables::t1", "csId": cs_id,
            "collection": "canonical_tables", "entityId": "t1", "op": op,
            "payload": payload if op != "delete" else None, "at": AT,
            "before": before, "beforeAt": before_at, "origin": origin}


def _hdr(cs_id: str, applied: str | None, status: str = "approved", owner: str = "u1",
         reviewed_by: str | None = "u2", approvals: dict | None = None, label: str = "v3") -> dict:
    return {"id": cs_id, "status": status, "appliedAt": applied, "owner": owner,
            "reviewedBy": reviewed_by, "versionLabel": label, "title": f"Versión {label}",
            "approvals": approvals if approvals is not None else {"u2": {"status": "approved", "at": AT}}}


# ── history_events (pura) ──────────────────────────────────────────────────


def test_clasificacion_y_orden_mas_reciente_primero():
    changes = [
        _ch("cs1", before=None, payload={"physicalName": "A", "logicalName": "a"}),
        _ch("cs2", before={"physicalName": "A"}, payload={"physicalName": "B", "logicalName": "a"}),
        _ch("cs3", op="delete", before={"physicalName": "B"}),
    ]
    headers = {"cs1": _hdr("cs1", "2026-08-01T00:00:00+00:00", label="v1"),
               "cs2": _hdr("cs2", "2026-08-05T00:00:00+00:00", label="v2"),
               "cs3": _hdr("cs3", "2026-08-09T00:00:00+00:00", label="v3")}
    ev = service.history_events(changes, headers)
    assert [e["action"] for e in ev] == ["deleted", "edited", "created"]
    assert [e["version"] for e in ev] == ["v3", "v2", "v1"]
    assert ev[2]["userId"] == "u1" and ev[2]["publishedById"] == "u2"
    assert ev[2]["approvedByIds"] == ["u2"]


def test_solo_changesets_aplicados_generan_eventos():
    # Draft, submitted, approved SIN appliedAt (publish interrumpido) y cambio
    # sin cabecera: NINGUNO publica historial — la garantía "solo publicado".
    changes = [_ch("draft"), _ch("subm"), _ch("interr"), _ch("huerfano")]
    headers = {"draft": _hdr("draft", None, status="draft"),
               "subm": _hdr("subm", None, status="submitted"),
               "interr": _hdr("interr", None, status="approved")}
    assert service.history_events(changes, headers) == []


def test_origin_solo_aflora_en_created():
    # El carry-forward del draft deja origin también en ediciones colapsadas:
    # mostrarlo ahí mentiría ("Created from…" sobre una tabla que ya existía).
    origin = {"kind": "paste-table", "sourceTable": "M_CLIENTE"}
    changes = [_ch("cs1", before=None, payload={"physicalName": "A"}, origin=origin),
               _ch("cs2", before={"physicalName": "A"}, payload={"physicalName": "B"}, origin=origin)]
    headers = {"cs1": _hdr("cs1", "2026-08-01T00:00:00+00:00"),
               "cs2": _hdr("cs2", "2026-08-05T00:00:00+00:00")}
    ev = service.history_events(changes, headers)
    assert ev[0]["action"] == "edited" and ev[0]["origin"] is None
    assert ev[1]["action"] == "created" and ev[1]["origin"] == origin


# ── entity_history (orquestación con mocks) ────────────────────────────────


def _wire(monkeypatch, *, changes, headers, published, earliest, users=None):
    # Doc 75: cabeceras y docs publicados llevan SIEMPRE su proyecto.
    headers = {k: {"projectId": "p1", **v} for k, v in headers.items()}
    published = [{"projectId": "p1", **d} for d in published]

    async def _changes(collection, entity_id):
        return changes

    async def _headers(ids):
        return headers

    async def _published(collection, flt=None, **kw):
        return published

    async def _earliest(project_id=None):
        return earliest

    async def _users():
        return users or []

    # Doc 80 §8: firma REAL (los catálogos son POR PROYECTO desde el doc 75).
    # El fake sin parámetro que había acá escondió durante días un TypeError
    # que en vivo era un 500 del historial.
    async def _udp(project_id):
        return [{"id": "udp1", "name": "Sensibilidad"}]

    async def _domains(project_id):
        return [{"id": "d1", "name": "Dominio Cliente"}]

    monkeypatch.setattr(service.repository, "entity_changes", _changes)
    monkeypatch.setattr(service.repository, "changesets_by_ids", _headers)
    monkeypatch.setattr(service.repository, "published", _published)
    monkeypatch.setattr(service.repository, "earliest_applied", _earliest)
    monkeypatch.setattr("app.features.auth.repository.list_users", _users)
    monkeypatch.setattr("app.features.udp.repository.list_udp", _udp)
    monkeypatch.setattr("app.features.domains.repository.list_domains", _domains)


V1 = {"id": "base", "appliedAt": "2026-07-24T00:00:00+00:00", "versionLabel": "v1",
      "title": "Base - Modelo DDV (XML)"}


def test_evento_editado_con_campos_y_usuarios_resueltos(monkeypatch):
    changes = [_ch("cs2", before={"id": "t1", "physicalName": "M_CLIENTE", "logicalName": "cliente",
                                  "schema": "core", "udpValues": {"udp1": "Alta"}},
                   payload={"physicalName": "M_CLIENTE", "logicalName": "cliente",
                            "schema": "core", "udpValues": {"udp1": "Baja"}})]
    _wire(monkeypatch, changes=changes, headers={"cs2": _hdr("cs2", "2026-08-05T00:00:00+00:00")},
          published=[{"id": "t1", "physicalName": "M_CLIENTE", "createdAt": "2026-07-20T00:00:00+00:00"}],
          earliest=V1,
          users=[{"id": "u1", "name": "Ana P", "email": "ana@x.pe", "role": "modeler"},
                 {"id": "u2", "name": "Rev", "email": "rev@x.pe", "role": "reviewer"}])
    out = asyncio.run(service.entity_history("canonical_tables", "t1"))
    edited, baseline = out["items"][0], out["items"][1]
    assert edited["action"] == "edited" and edited["version"] == "v3"
    assert edited["user"] == {"id": "u1", "name": "Ana P", "email": "ana@x.pe"}
    assert edited["publishedBy"]["email"] == "rev@x.pe"
    assert edited["approvedBy"][0]["id"] == "u2"
    # El detalle sale del MISMO motor del popup de revisión: UDP por NOMBRE.
    labels = {f["label"]: (f["before"], f["after"]) for f in edited["fields"]}
    assert labels["UDP · Sensibilidad"] == ("Alta", "Baja")
    # Sin evento created ⇒ entrada sintética al final con la fecha REAL de
    # carga (createdAt del doc) y la versión base.
    assert baseline["synthetic"] is True and baseline["action"] == "created"
    assert baseline["origin"] == {"kind": "migration"}
    assert baseline["at"] == "2026-07-20T00:00:00+00:00" and baseline["version"] == "v1"


def test_entidad_migrada_sin_eventos_solo_sintetica(monkeypatch):
    # Doc SIN createdAt (legacy): la fecha cae al appliedAt del marcador v1.
    _wire(monkeypatch, changes=[], headers={}, published=[{"id": "t1", "physicalName": "M_CLIENTE"}],
          earliest=V1)
    out = asyncio.run(service.entity_history("canonical_tables", "t1"))
    assert len(out["items"]) == 1
    assert out["items"][0]["synthetic"] is True
    assert out["items"][0]["at"] == V1["appliedAt"]


def test_entidad_solo_draft_sin_historial(monkeypatch):
    # Creada en una working copy que nunca publicó: nada que auditar.
    _wire(monkeypatch, changes=[], headers={}, published=[], earliest=V1)
    assert asyncio.run(service.entity_history("canonical_tables", "t1")) == {"items": []}


def test_created_real_no_agrega_sintetica(monkeypatch):
    changes = [_ch("cs1", before=None,
                   payload={"physicalName": "M_NUEVA", "logicalName": "nueva", "schema": "core"})]
    _wire(monkeypatch, changes=changes, headers={"cs1": _hdr("cs1", "2026-08-05T00:00:00+00:00")},
          published=[{"id": "t1", "physicalName": "M_NUEVA", "createdAt": "2026-08-05T00:00:00+00:00"}],
          earliest=V1, users=[{"id": "u1", "name": "Ana P", "email": "ana@x.pe", "role": "m"}])
    out = asyncio.run(service.entity_history("canonical_tables", "t1"))
    assert [i["action"] for i in out["items"]] == ["created"]
    assert out["items"][0]["synthetic"] is False


# ── origin: carry-forward en el repository (puro) ──────────────────────────


def test_origin_explicito_se_adjunta_y_delete_lo_corta():
    origin = {"kind": "ctas"}
    doc = repository._with_origin({"op": "upsert"}, origin, None)
    assert doc["origin"] == origin
    # Un delete no lleva procedencia aunque el cambio previo la tuviera.
    doc = repository._with_origin({"op": "delete"}, None, {"op": "upsert", "origin": origin})
    assert "origin" not in doc


def test_origin_se_arrastra_en_reedicion_del_draft():
    prev = {"op": "upsert", "origin": {"kind": "paste-columns", "sourceColumn": "ID"}}
    doc = repository._with_origin({"op": "upsert"}, None, prev)
    assert doc["origin"] == prev["origin"]
    # Si la re-edición trae su PROPIO origin, gana el nuevo.
    doc = repository._with_origin({"op": "upsert"}, {"kind": "ctas"}, prev)
    assert doc["origin"] == {"kind": "ctas"}


def test_change_body_acepta_origin_opcional():
    body = ChangeBody.model_validate({"collection": "canonical_tables", "entityId": "t1",
                                      "op": "upsert", "payload": {"a": 1},
                                      "origin": {"kind": "paste-table", "sourceTable": "X"}})
    assert body.origin == {"kind": "paste-table", "sourceTable": "X"}
    assert ChangeBody.model_validate({"collection": "c", "entityId": "e", "op": "delete"}).origin is None
