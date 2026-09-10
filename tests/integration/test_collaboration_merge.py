"""Trabajo colaborativo = merge tipo git (doc 82, pedido del owner):

- dos personas abren su draft desde la MISMA producción;
- la primera publica: la segunda ve esos cambios en su draft SIN hacer nada
  («git pull» automático: el draft es un overlay sobre lo publicado vivo);
- la segunda publica: NO pisa lo que la primera tocó en otras entidades;
  sólo gana en las entidades que AMBAS tocaron — y esas llegan al revisor
  marcadas como conflicto en el diff.

El nivel de «objeto» es el documento: una columna es un doc, una tabla es
otro. Tocar columnas distintas de la misma tabla NO es conflicto."""
from __future__ import annotations

from tests.integration.conftest import column_payload, publish


def _cols(api_user, table_id: str) -> dict[str, dict]:
    return {c["physicalName"]: c for c in api_user.get(f"/api/catalog/tables/{table_id}/columns")}


def test_second_publisher_does_not_clobber_the_first(api, world):
    pid, t1, t2, c_a, c_b = world["pid"], world["t1"], world["t2"], world["c_a"], world["c_b"]
    ana, carla, beto = api("ana"), api("carla"), api("beto")
    base = _cols(ana, t1)
    col_a, col_b = base["CODCLIENTE"], base["NBRCLIENTE"]

    # Dos drafts desde la misma producción (v2), labels autoincrementales del proyecto.
    dx = ana.post("/api/changesets/snapshot", {"projectId": pid}, expect=201)
    dy = carla.post("/api/changesets/snapshot", {"projectId": pid}, expect=201)
    assert {dx["versionLabel"], dy["versionLabel"]} == {"v3", "v4"}
    dx, dy = dx["id"], dy["id"]

    # Y (carla) toca A y B primero; X (ana) toca A y agrega D en la otra tabla.
    carla.change(dy, "canonical_columns", c_a, {**col_a, "description": "A por Carla"})
    carla.change(dy, "canonical_columns", c_b, {**col_b, "description": "B por Carla"})
    ana.change(dx, "canonical_columns", c_a, {**col_a, "description": "A por Ana"})
    d_id = f"{t2}-col-saldo"
    ana.change(dx, "canonical_columns", d_id, column_payload(t2, "MTOSALDO", "Monto Saldo", 1, data_type="DECIMAL(18,2)"))

    # X publica primero.
    publish(api, dx, owner="ana")
    prod = _cols(ana, t1)
    assert prod["CODCLIENTE"]["description"] == "A por Ana" and prod["NBRCLIENTE"].get("description") is None
    assert "MTOSALDO" in _cols(ana, t2)

    # «git pull» automático: el draft de Y ya ve MTOSALDO (no la tocó) y
    # conserva SUS ediciones sobre A y B.
    y_t2 = {c["physicalName"] for c in carla.get(f"/api/changesets/{dy}/effective/canonical_columns?tableId={t2}")}
    assert y_t2 == {"CODCLIENTE", "MTOSALDO"}
    y_t1 = {c["physicalName"]: c for c in carla.get(f"/api/changesets/{dy}/effective/canonical_columns?tableId={t1}")}
    assert y_t1["CODCLIENTE"]["description"] == "A por Carla" and y_t1["NBRCLIENTE"]["description"] == "B por Carla"

    # El revisor ve el conflicto SOLO en la entidad que ambas tocaron.
    carla.post(f"/api/changesets/{dy}/submit", {"title": "Cambios de Carla", "reviewers": ["beto"]})
    diff = beto.get(f"/api/changesets/{dy}/diff")
    edited = {e["id"]: e for e in diff["collections"]["canonical_columns"]["edited"]}
    assert edited[c_a]["conflict"] is True and edited[c_b]["conflict"] is False
    details = beto.post(f"/api/changesets/{dy}/diff/details",
                        {"items": [{"collection": "canonical_columns", "entityId": c_a}]})
    desc = next(f for f in details["items"][0]["fields"] if f["key"] == "description")
    assert desc["before"] == "A por Ana" and desc["after"] == "A por Carla"   # «antes» = producción VIVA

    # Y publica: gana en A (conflicto asumido), B es suya, D de X sobrevive.
    approved = beto.post(f"/api/changesets/{dy}/review", {"decision": "approve"})
    assert approved["status"] == "approved"
    prod = _cols(ana, t1)
    assert prod["CODCLIENTE"]["description"] == "A por Carla"
    assert prod["NBRCLIENTE"]["description"] == "B por Carla"
    assert "MTOSALDO" in _cols(ana, t2)

    # El historial de A cuenta la historia completa, con autor por versión.
    hist = ana.get(f"/api/changesets/history/canonical_columns/{c_a}")["items"]
    assert [(h["action"], h["user"]["id"]) for h in hist] == [("edited", "carla"), ("edited", "ana"), ("created", "ana")]
    # Producción = última publicada del proyecto; el compare entre las dos
    # publicaciones muestra sólo lo que cambió NETO entre ellas.
    published = ana.get(f"/api/projects/{pid}/versions/published")
    assert published["id"] == dy
    cmp = ana.get(f"/api/versions/compare?fromId={dx}&toId={dy}")
    assert {e["id"] for e in cmp["collections"]["canonical_columns"]["edited"]} == {c_a, c_b}


def test_conflict_flag_respects_the_column_granularity(api, world):
    """Dos personas editando columnas DISTINTAS de la MISMA tabla no chocan,
    aunque la primera publique en el medio."""
    pid, t1, c_a, c_b = world["pid"], world["t1"], world["c_a"], world["c_b"]
    ana, carla, beto = api("ana"), api("carla"), api("beto")
    base = _cols(ana, t1)
    dx = ana.post("/api/changesets/snapshot", {"projectId": pid}, expect=201)["id"]
    dy = carla.post("/api/changesets/snapshot", {"projectId": pid}, expect=201)["id"]
    carla.change(dy, "canonical_columns", c_b, {**base["NBRCLIENTE"], "logicalName": "Nombre (Carla)"})
    ana.change(dx, "canonical_columns", c_a, {**base["CODCLIENTE"], "logicalName": "Codigo (Ana)"})
    publish(api, dx, owner="ana")
    carla.post(f"/api/changesets/{dy}/submit", {"reviewers": ["beto"]})
    diff = beto.get(f"/api/changesets/{dy}/diff")
    assert all(e["conflict"] is False for e in diff["collections"]["canonical_columns"]["edited"])
    beto.post(f"/api/changesets/{dy}/review", {"decision": "approve"})
    prod = _cols(ana, t1)
    assert prod["CODCLIENTE"]["logicalName"] == "Codigo (Ana)" and prod["NBRCLIENTE"]["logicalName"] == "Nombre (Carla)"
