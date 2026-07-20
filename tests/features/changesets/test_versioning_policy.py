"""Política de versionado/aprobación (R1b) — funciones puras, sin DB."""
from __future__ import annotations

from app.features.changesets import service


# ── next_version_label ────────────────────────────────────────────────────


def test_next_version_label_starts_at_v1_when_empty():
    assert service.next_version_label([]) == "v1"
    assert service.next_version_label(None) == "v1"


def test_next_version_label_ignores_non_matching_and_takes_max():
    assert service.next_version_label(["v3", "v1", "foo", None, "v10"]) == "v11"


def test_next_version_label_case_insensitive():
    assert service.next_version_label(["V2"]) == "v3"


# ── is_assigned ───────────────────────────────────────────────────────────


def test_is_assigned():
    assert service.is_assigned(["ana", "beto"], "ana") is True
    assert service.is_assigned([], "ana") is False
    assert service.is_assigned(None, "ana") is False


# ── record_approval ───────────────────────────────────────────────────────


def test_record_approval_maps_decision_and_keeps_immutable_input():
    approvals: dict = {}
    out = service.record_approval(approvals, "ana", "approve", None)
    assert out["ana"]["status"] == "approved"
    assert "at" in out["ana"]
    assert "note" not in out["ana"]
    assert approvals == {}  # no muta el input


def test_record_approval_reject_with_note():
    out = service.record_approval({}, "beto", "reject", "faltan PKs")
    assert out["beto"]["status"] == "rejected"
    assert out["beto"]["note"] == "faltan PKs"


# ── approval_outcome ──────────────────────────────────────────────────────


def test_approval_outcome_all_approved():
    approvals = {"ana": {"status": "approved"}, "beto": {"status": "approved"}}
    assert service.approval_outcome(["ana", "beto"], approvals) == "approved"


def test_approval_outcome_any_rejected():
    approvals = {"ana": {"status": "approved"}, "beto": {"status": "rejected"}}
    assert service.approval_outcome(["ana", "beto"], approvals) == "rejected"


def test_approval_outcome_partial_is_pending():
    approvals = {"ana": {"status": "approved"}}
    assert service.approval_outcome(["ana", "beto"], approvals) == "pending"


def test_approval_outcome_no_reviewers_is_pending():
    assert service.approval_outcome([], {}) == "pending"


# ── structured_diff + impacto ─────────────────────────────────────────────


def test_structured_diff_buckets_and_impact():
    published = {
        "canonical_tables": [
            {"id": "t1", "physicalName": "CUENTA"},
            {"id": "t2", "physicalName": "CLIENTE"},
            {"id": "t3", "physicalName": "SUCURSAL"},
        ],
        "canonical_columns": [
            {"id": "c1", "tableId": "t1", "physicalName": "ID_CUENTA"},
        ],
    }
    relationships = [
        {"sourceTableId": "t1", "targetTableId": "t2"},  # t1 tocada → t2 afectada
        {"sourceTableId": "t3", "targetTableId": "t2"},  # ninguna tocada → no cuenta
    ]
    changes = {
        "canonical_tables": {
            "t1": {"op": "upsert", "payload": {"physicalName": "CUENTA"}},  # edited (existe)
            "t9": {"op": "upsert", "payload": {"physicalName": "NUEVA"}},   # added (no existe)
        },
        "canonical_columns": {
            "c1": {"op": "delete"},  # deleted
        },
    }

    out = service.structured_diff(changes, published, relationships)

    tcols = out["collections"]["canonical_tables"]
    added_ids = {x["id"] for x in tcols["added"]}
    edited_ids = {x["id"] for x in tcols["edited"]}
    assert added_ids == {"t9"}
    assert edited_ids == {"t1"}

    ccols = out["collections"]["canonical_columns"]
    assert {x["id"] for x in ccols["deleted"]} == {"c1"}

    impact = out["impact"]
    # tablas tocadas: t1, t9 (tablas) + t1 (de la columna c1) = {t1, t9}
    assert impact["tablesTouched"] == 2
    assert impact["columnsTouched"] == 1
    affected_ids = {x["id"] for x in impact["affectedOtherTables"]}
    assert affected_ids == {"t2"}  # conectada a t1 (tocada), y no está tocada


# ── version_row ───────────────────────────────────────────────────────────


def test_version_row_projection():
    cs = {
        "id": "cs1", "versionLabel": "v3", "title": "Add Chapters", "owner": "ana",
        "status": "submitted", "projectIds": ["p1", "p2"], "reviewers": ["beto"],
        "createdAt": "t0", "updatedAt": "t1", "submittedAt": "t2",
        "changes": {"canonical_tables": {"t1": {"op": "upsert"}}},  # NO debe filtrarse
    }
    row = service.version_row(cs)
    assert row["id"] == "cs1" and row["versionLabel"] == "v3"
    assert row["projectIds"] == ["p1", "p2"] and row["reviewers"] == ["beto"]
    assert "changes" not in row  # la fila de versión no expone el diff crudo


# ── structured_diff: conflictos vs producción ──────────────────────────────


def test_structured_diff_conflicto_vs_produccion():
    """`conflict=True` sólo cuando producción cambió DESPUÉS de que el changeset
    editó la entidad (publicado.updatedAt > `at` del cambio)."""
    published = {
        "canonical_tables": [
            {"id": "t1", "physicalName": "CUENTA", "updatedAt": "2026-07-02T12:00:00+00:00"},
            {"id": "t2", "physicalName": "CLIENTE", "updatedAt": "2026-07-01T09:00:00+00:00"},
        ],
    }
    changes = {
        "canonical_tables": {
            # editada ANTES del cambio publicado → conflicto
            "t1": {"op": "upsert", "payload": {"physicalName": "CUENTA"}, "at": "2026-07-02T10:00:00+00:00"},
            # editada DESPUÉS del último cambio publicado → sin conflicto
            "t2": {"op": "upsert", "payload": {"physicalName": "CLIENTE"}, "at": "2026-07-01T10:00:00+00:00"},
            # nueva (no existe en producción) → nunca conflicto
            "t9": {"op": "upsert", "payload": {"physicalName": "NUEVA"}, "at": "2026-07-01T10:00:00+00:00"},
        },
    }
    out = service.structured_diff(changes, published, baseline="2026-06-30T00:00:00+00:00")
    by_id = {x["id"]: x for b in out["collections"]["canonical_tables"].values() for x in b}
    assert by_id["t1"]["conflict"] is True
    assert by_id["t2"]["conflict"] is False
    assert by_id["t9"]["conflict"] is False


def test_structured_diff_conflicto_fallback_baseline():
    """Cambios sin `at` (docs viejos) usan el createdAt del changeset como
    baseline; sin ningún timestamp no se puede juzgar → sin flag (conservador)."""
    published = {"canonical_tables": [
        {"id": "t1", "physicalName": "CUENTA", "updatedAt": "2026-07-02T12:00:00+00:00"},
    ]}
    changes = {"canonical_tables": {"t1": {"op": "delete"}}}

    con_baseline = service.structured_diff(changes, published, baseline="2026-07-01T00:00:00+00:00")
    assert con_baseline["collections"]["canonical_tables"]["deleted"][0]["conflict"] is True

    sin_baseline = service.structured_diff(changes, published)
    assert sin_baseline["collections"]["canonical_tables"]["deleted"][0]["conflict"] is False


# ── submit / reopen: guards de la máquina de estados (repository mockeado) ──
# service.submit/reopen orquestan repository + guards puros; con AsyncMock no
# hace falta DB y validamos EXACTAMENTE la transición pedida.

import asyncio
from unittest.mock import AsyncMock


def test_submit_no_reenvia_un_request_ya_submitted(monkeypatch):
    """Re-submitir un request en revisión pisaba título/revisores del envío
    anterior: ahora sólo se envía desde draft (el owner retira primero)."""
    monkeypatch.setattr(service.repository, "get",
                        AsyncMock(return_value={"id": "c1", "status": "submitted", "owner": "ana"}))
    assert asyncio.run(service.submit("c1", "ana")) is None


def test_submit_solo_el_owner(monkeypatch):
    monkeypatch.setattr(service.repository, "get",
                        AsyncMock(return_value={"id": "c1", "status": "draft", "owner": "ana"}))
    assert asyncio.run(service.submit("c1", "beto", reviewers=["qa"])) == "forbidden"


def test_submit_transicion_atomica_desde_draft_con_approvals_reseteado(monkeypatch):
    monkeypatch.setattr(service.repository, "get",
                        AsyncMock(return_value={"id": "c1", "status": "draft", "owner": "ana"}))
    tr = AsyncMock(return_value={"id": "c1", "status": "submitted"})
    monkeypatch.setattr(service.repository, "transition", tr)

    res = asyncio.run(service.submit("c1", "ana", title="T", reviewers=["qa"]))

    assert res == {"id": "c1", "status": "submitted"}
    cs_id, from_status, fields = tr.await_args.args
    assert (cs_id, from_status) == ("c1", "draft")
    assert fields["status"] == "submitted"
    assert fields["approvals"] == {}  # nada heredado de un ciclo anterior
    assert fields["reviewers"] == ["qa"] and fields["title"] == "T"
    assert fields["submittedAt"]  # timestamp fresco (no el del envío previo)


def test_reopen_solo_rejected_y_solo_owner(monkeypatch):
    monkeypatch.setattr(service.repository, "get",
                        AsyncMock(return_value={"id": "c1", "status": "submitted", "owner": "ana"}))
    assert asyncio.run(service.reopen("c1", "ana")) is None  # no está rejected

    monkeypatch.setattr(service.repository, "get",
                        AsyncMock(return_value={"id": "c1", "status": "rejected", "owner": "ana"}))
    assert asyncio.run(service.reopen("c1", "beto")) == "forbidden"


def test_reopen_vuelve_a_draft_y_limpia_metadata_de_review(monkeypatch):
    """El reject NUNCA borra la versión; reopen la devuelve a draft limpia
    (decisiones y nota de rechazo fuera) para corregir y re-enviar."""
    monkeypatch.setattr(service.repository, "get",
                        AsyncMock(return_value={"id": "c1", "status": "rejected", "owner": "ana"}))
    tr = AsyncMock(return_value={"id": "c1", "status": "draft"})
    monkeypatch.setattr(service.repository, "transition", tr)

    res = asyncio.run(service.reopen("c1", "ana"))

    assert res == {"id": "c1", "status": "draft"}
    cs_id, from_status, fields = tr.await_args.args
    assert (cs_id, from_status) == ("c1", "rejected")
    assert fields == {"status": "draft", "approvals": {}, "submittedAt": None,
                      "reviewedBy": None, "reviewedAt": None, "reviewNote": None}


def test_apply_and_finalize_reclama_el_estado_antes_de_aplicar(monkeypatch):
    """El cierre approved RECLAMA submitted→approved ANTES de tocar producción:
    si un withdraw concurrente ganó (transition devuelve None), NO se aplica nada.
    Los cambios se leen DESPUÉS del claim (congelados: add_change exige draft)."""
    tr = AsyncMock(return_value=None)  # otro flujo se llevó el estado
    apply = AsyncMock()
    cm = AsyncMock(return_value={"canonical_tables": {"t1": {"op": "delete"}}})
    monkeypatch.setattr(service.repository, "transition", tr)
    monkeypatch.setattr(service.repository, "apply_changes", apply)
    monkeypatch.setattr(service.repository, "changes_map", cm)

    res = asyncio.run(service._apply_and_finalize("c1", {"status": "approved"}, "T1"))

    assert res is None
    apply.assert_not_awaited()  # producción intacta: no se publicaron cambios retirados
    cm.assert_not_awaited()     # ni siquiera se leyeron los cambios
    # El claim va condicionado al submittedAt del envío decidido (anti-ABA):
    # un withdraw→edit→resubmit cambia submittedAt y el claim tardío falla.
    assert tr.await_args.kwargs["expect"] == {"submittedAt": "T1"}

    # Camino feliz: reclamado el estado, recién ahí se lee, aplica y estampa appliedAt.
    tr2 = AsyncMock(return_value={"id": "c1", "status": "approved"})
    apply2 = AsyncMock(return_value={"canonical_tables": 1})
    st = AsyncMock(return_value={"id": "c1", "status": "approved", "appliedAt": "T9"})
    # Captura de imágenes previas (rollback, doc 16 §5d): ocurre ANTES del apply.
    cap = AsyncMock(return_value={("canonical_tables", "t1"): {"id": "t1"}})
    store = AsyncMock()
    monkeypatch.setattr(service.repository, "transition", tr2)
    monkeypatch.setattr(service.repository, "apply_changes", apply2)
    monkeypatch.setattr(service.repository, "set_status", st)
    monkeypatch.setattr(service.repository, "capture_before_images", cap)
    monkeypatch.setattr(service.repository, "store_before_images", store)
    res2 = asyncio.run(service._apply_and_finalize("c1", {"status": "approved"}, "T1"))
    assert res2["appliedAt"] == "T9"
    apply2.assert_awaited_once()
    plan = apply2.await_args.args[0]
    assert plan == [("canonical_tables", "t1", "delete", None)]
    assert "appliedAt" in st.await_args.args[1]
    store.assert_awaited_once_with("c1", {("canonical_tables", "t1"): {"id": "t1"}})


def test_rollback_plan_invierte_con_imagenes_previas():
    """Inverso por entidad: before=doc → upsert del doc previo; before=None
    (la entidad se CREÓ en la versión) → delete. Puro."""
    changes = {
        "canonical_tables": {
            "t1": {"op": "upsert", "payload": {"x": 2}, "beforeAt": "T0",
                   "before": {"id": "t1", "x": 1}},
            "t2": {"op": "upsert", "payload": {"x": 9}, "beforeAt": "T0", "before": None},
        },
        "projects": {"p1": {"op": "delete", "beforeAt": "T0",
                            "before": {"id": "p1", "name": "P"}}},
    }
    inverse, missing = service.rollback_plan(changes)
    assert missing == []
    by = {(c["collection"], c["entityId"]): c for c in inverse}
    assert by[("canonical_tables", "t1")]["op"] == "upsert"
    assert by[("canonical_tables", "t1")]["payload"] == {"x": 1}   # sin 'id'
    assert by[("canonical_tables", "t2")]["op"] == "delete"        # creada → borrar
    assert by[("projects", "p1")]["op"] == "upsert"                # borrada → restaurar
    assert by[("projects", "p1")]["payload"] == {"name": "P"}


def test_rollback_plan_aborta_si_falta_alguna_imagen():
    """Un solo cambio sin imagen previa (publicado pre-feature) invalida el
    rollback completo — restaurar a medias dejaría un estado inconsistente."""
    inverse, missing = service.rollback_plan(
        {"views": {"v1": {"op": "upsert", "payload": {}}}})
    assert missing == ["views/v1"]


def test_apply_and_finalize_payload_invalido_revierte_y_no_aplica(monkeypatch):
    """Gate autoritativo del apply: un payload legacy que no valida contra su
    modelo NO entra a producción — se revierte el claim (limpiando decisiones,
    como withdraw) y se levanta 422."""
    import pytest

    from app.features.changesets.validation import InvalidPayloadError

    tr = AsyncMock(return_value={"id": "c1", "status": "approved"})
    apply = AsyncMock()
    # Columna sin tableId/physicalName/dataType: inválida.
    cm = AsyncMock(return_value={"canonical_columns": {"c9": {"op": "upsert", "payload": {"logicalName": "x"}}}})
    monkeypatch.setattr(service.repository, "transition", tr)
    monkeypatch.setattr(service.repository, "apply_changes", apply)
    monkeypatch.setattr(service.repository, "changes_map", cm)

    with pytest.raises(InvalidPayloadError):
        asyncio.run(service._apply_and_finalize("c1", {"status": "approved"}, "T1"))

    apply.assert_not_awaited()  # producción intacta
    # Dos transiciones: el claim (submitted→approved) y el revert (approved→submitted).
    assert tr.await_count == 2
    revert_args = tr.await_args_list[1].args
    assert revert_args[1] == "approved"
    assert revert_args[2]["status"] == "submitted"
    # El revert deja el header coherente: sin approvals residuales que
    # re-dispararían el apply fallido en la próxima decisión de CUALQUIER revisor.
    assert revert_args[2]["approvals"] == {}


def test_apply_and_finalize_fallo_de_apply_devuelve_a_revision(monkeypatch):
    """Si el bulk de apply FALLA (throttling/timeout de Cosmos), el request
    vuelve a `submitted` (el apply es idempotente: re-aprobar reintenta) y la
    excepción se propaga — nunca queda `approved` con producción a medias por
    un error recuperable."""
    import pytest

    tr = AsyncMock(return_value={"id": "c1", "status": "approved"})
    apply = AsyncMock(side_effect=RuntimeError("cosmos 429"))
    cm = AsyncMock(return_value={"canonical_tables": {"t1": {"op": "delete"}}})
    monkeypatch.setattr(service.repository, "transition", tr)
    monkeypatch.setattr(service.repository, "apply_changes", apply)
    monkeypatch.setattr(service.repository, "changes_map", cm)

    with pytest.raises(RuntimeError):
        asyncio.run(service._apply_and_finalize("c1", {"status": "approved"}, "T1"))

    assert tr.await_count == 2  # claim + revert
    revert_args = tr.await_args_list[1].args
    assert revert_args[2]["status"] == "submitted" and revert_args[2]["approvals"] == {}


def test_changes_in_cycle_excluye_escrituras_tardias():
    """La ventana de compensación de set_change puede dejar visible una
    escritura POSTERIOR al envío entre el claim y la lectura del apply: el
    filtro por `at <= submittedAt` la deja fuera del publish. Cambios sin `at`
    (legacy) se conservan."""
    changes = {
        "canonical_tables": {
            "t1": {"op": "delete", "at": "2026-07-04T10:00:00+00:00"},          # en ciclo
            "t2": {"op": "upsert", "payload": {}, "at": "2026-07-04T12:00:01+00:00"},  # tardía
            "t3": {"op": "delete"},                                              # legacy sin at
        },
    }
    out = service.changes_in_cycle(changes, "2026-07-04T12:00:00+00:00")
    assert set(out["canonical_tables"]) == {"t1", "t3"}
    # Sin submittedAt (compat) no filtra nada.
    assert service.changes_in_cycle(changes, None) == changes


def test_apply_plan_ordena_por_dependencia():
    """El apply escribe una colección por vez sin transacción: el plan va en
    orden de VERSIONED (tablas antes que columnas antes que relaciones) para
    que un fallo a mitad no publique hijos sin su padre."""
    changes = {
        "relationships": {"r1": {"op": "upsert", "payload": {}}},
        "canonical_columns": {"c1": {"op": "upsert", "payload": {}}},
        "canonical_tables": {"t1": {"op": "upsert", "payload": {}}},
    }
    plan = service.apply_plan(changes)
    colls = [p[0] for p in plan]
    assert colls == ["canonical_tables", "canonical_columns", "relationships"]


def test_current_production_prefiere_applied(monkeypatch):
    """Una `approved` SIN appliedAt es un publish interrumpido: no puede
    mostrarse como la producción vigente aunque sea la más reciente."""
    rows = [
        {"id": "a", "status": "approved", "reviewedAt": "T5"},                      # interrumpida (sin appliedAt)
        {"id": "b", "status": "approved", "reviewedAt": "T3", "appliedAt": "T4"},   # aplicada
        {"id": "c", "status": "submitted"},
    ]
    monkeypatch.setattr(service.repository, "list_summaries", AsyncMock(return_value=rows))
    row = asyncio.run(service.current_production())
    assert row["id"] == "b"


def test_add_change_es_owner_only(monkeypatch):
    """La working copy es personal: escribir cambios exige ser el owner (el
    backstop de los guards owner-only de submit/withdraw/reopen)."""
    monkeypatch.setattr(service.repository, "get",
                        AsyncMock(return_value={"id": "c1", "status": "draft", "owner": "ana"}))
    res = asyncio.run(service.add_change("c1", "beto", "canonical_tables", "t1", "delete", None))
    assert res == "forbidden"

    set_change = AsyncMock(return_value={"id": "c1", "status": "draft"})
    monkeypatch.setattr(service.repository, "set_change", set_change)
    res_ok = asyncio.run(service.add_change("c1", "ana", "canonical_tables", "t1", "delete", None))
    assert res_ok == {"id": "c1", "status": "draft"}


def test_add_change_rechaza_payload_invalido_sin_escribir(monkeypatch):
    """El gate de ENTRADA: un upsert que no valida contra el modelo de su
    colección levanta InvalidPayloadError (→ 422) y no llega al repository."""
    import pytest

    from app.features.changesets.validation import InvalidPayloadError

    monkeypatch.setattr(service.repository, "get",
                        AsyncMock(return_value={"id": "c1", "status": "draft", "owner": "ana"}))
    set_change = AsyncMock()
    monkeypatch.setattr(service.repository, "set_change", set_change)

    with pytest.raises(InvalidPayloadError):
        asyncio.run(service.add_change("c1", "ana", "canonical_columns", "c9", "upsert",
                                       {"logicalName": "sin lo demás"}))
    set_change.assert_not_awaited()


def test_review_exige_revisor_asignado_en_ambas_decisiones(monkeypatch):
    """Los compat /approve y /reject ahora DELEGAN en review(): exigen estar en
    reviewers[] (un no-asignado recibe 'forbidden' → 403) para approve y reject.
    Antes /approve aplicaba con una sola aprobación, salteando la unanimidad."""
    monkeypatch.setattr(service.repository, "get",
                        AsyncMock(return_value={"id": "c1", "status": "submitted",
                                                "owner": "ana", "reviewers": ["beto"]}))
    assert asyncio.run(service.review("c1", "qa", "approve", None)) == "forbidden"
    assert asyncio.run(service.review("c1", "qa", "reject", None)) == "forbidden"
