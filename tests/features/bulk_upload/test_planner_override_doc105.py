"""Doc 105 — la re-carga idéntica de un Excel queda «unchanged» aunque el
físico no siga la regla de naming. El changeset estampa
`physicalNameOverridden=True` en esos físicos (doc 68) y el planner arma sus
docs con el flag en False: la comparación veía un cambio que la hoja no trae
(«update», aviso con el flag interno y ediciones vacías en el draft). Puro."""
from __future__ import annotations

from app.features.bulk_upload.planner import build_plan

from .helpers import crow, ctx, parsed, trow

_SCHEMAS = [{"id": "s1", "name": "ddv", "kind": "tables"}]


def _table(physical="MI_CLIENTE", description=None):
    # Físico custom (el derivado sería CLIENTE) → el publicado lleva el flag.
    return {"id": "t1", "projectId": "p1", "physicalName": physical, "logicalName": "Cliente", "schema": "ddv",
            "description": description, "udpValues": {}, "physicalNameOverridden": True}


def _col(physical="COD_CLI"):
    return {"id": "c1", "projectId": "p1", "tableId": "t1", "physicalName": physical, "logicalName": "Codigo",
            "parentDomainId": None, "dataType": "STRING", "typeOverridden": False, "isPrimaryKey": None,
            "isForeignKey": None, "isNullable": True, "isPartition": False, "description": None, "ordinal": 0,
            "udpValues": {}, "physicalNameOverridden": True}


def test_recarga_identica_de_tabla_con_fisico_custom_queda_unchanged():
    plan = build_plan(parsed(tables=[trow(3, "Cliente", physical="MI_CLIENTE", schema="ddv")]),
                      ctx(schemas=_SCHEMAS, tables=[_table()]))
    assert plan.changes == []
    assert "existing-table" not in [w["code"] for w in plan.report["warnings"]]
    assert plan.report["summary"]["tables"] == {"create": 0, "update": 0, "unchanged": 1}


def test_recarga_identica_de_columna_con_fisico_custom_queda_unchanged():
    c = ctx(schemas=_SCHEMAS, tables=[_table()], columns_by_table={"t1": [_col()]})
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Codigo", physical="COD_CLI")]), c)
    assert plan.changes == [] and plan.report["warnings"] == []
    assert plan.report["summary"]["columns"] == {"create": 0, "update": 0, "unchanged": 1}


def test_un_cambio_real_sigue_siendo_update_y_el_aviso_no_nombra_el_flag():
    plan = build_plan(parsed(tables=[trow(3, "Cliente", physical="MI_CLIENTE", schema="ddv", description="Def")]),
                      ctx(schemas=_SCHEMAS, tables=[_table()]))
    assert plan.report["summary"]["tables"] == {"create": 0, "update": 1, "unchanged": 0}
    existing = next(w for w in plan.report["warnings"] if w["code"] == "existing-table")
    assert existing["message"].endswith("it will be updated (description).")


def test_cambiar_la_grafia_del_fisico_sigue_siendo_update():
    # La identidad de tabla es por físico sin mayúsculas: cambiar la grafía es
    # un renombre real (las tablas conservan la grafía tipeada).
    plan = build_plan(parsed(tables=[trow(3, "Cliente", physical="mi_cliente", schema="ddv")]),
                      ctx(schemas=_SCHEMAS, tables=[_table()]))
    assert plan.report["summary"]["tables"] == {"create": 0, "update": 1, "unchanged": 0}
    assert "rename" in [w["code"] for w in plan.report["warnings"]]


# ── Revisión R2: un update real BAJABA el override en silencio ────────────
# Override (`physicalNameOverridden=True`) cuyo físico HOY coincide con el
# derivado (el glosario cambió después, o el modelador lo fijó igual). Con un
# cambio real, el doc planeado llevaba el flag en False; el changeset estampa
# «flag del payload OR físico≠derivado» = False → el publish quitaba el
# override (y el próximo Save & apply del glosario ya podía renombrarlo),
# mientras el reporte decía «updated (description)». Ahora el flag existente
# se conserva si el físico no cambia (como `logicalOnly`/`physicalOnly`).

from app.features.bulk_upload.plan_tables import changed_fields  # noqa: E402
from app.features.glossary.service import is_physical_override  # noqa: E402


def _stamped(payload: dict) -> bool:
    """Lo que estampa el changeset con las reglas del `ctx()` de los tests
    (sin glosario, join + UPPER)."""
    return is_physical_override(payload, {}, "", "upper")


def _change(plan, coll: str) -> dict:
    (ch,) = [c for c in plan.changes if c["collection"] == coll]
    return ch


def _message(plan, code: str) -> str:
    return next(w["message"] for w in plan.report["warnings"] if w["code"] == code)


def test_r2_update_real_de_tabla_conserva_el_override_con_fisico_igual_al_derivado():
    existing = _table(physical="CLIENTE")                         # derivado de «Cliente» = CLIENTE
    plan = build_plan(parsed(tables=[trow(3, "Cliente", physical="CLIENTE", schema="ddv", description="Def")]),
                      ctx(schemas=_SCHEMAS, tables=[existing]))
    payload = _change(plan, "canonical_tables")["payload"]
    assert payload["physicalNameOverridden"] is True, payload
    assert _stamped(payload) is True
    assert _message(plan, "existing-table").endswith("it will be updated (description).")


def test_r2_update_sin_fisico_declarado_tambien_conserva_el_override():
    plan = build_plan(parsed(tables=[trow(3, "Cliente", description="Def")]),
                      ctx(schemas=_SCHEMAS, tables=[_table(physical="CLIENTE")]))
    assert _change(plan, "canonical_tables")["payload"]["physicalNameOverridden"] is True


def test_r2_update_real_de_columna_conserva_el_override_con_fisico_igual_al_derivado():
    c = ctx(schemas=_SCHEMAS, tables=[_table()], columns_by_table={"t1": [_col(physical="CODIGO")]})
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Codigo", physical="CODIGO", description="Def")]), c)
    payload = _change(plan, "canonical_columns")["payload"]
    assert payload["physicalNameOverridden"] is True, payload
    assert _stamped(payload) is True
    assert _message(plan, "existing-column").endswith("it will be updated (description).")


def _model_changes(plan) -> list[dict]:
    """Cambios de tablas/columnas (la fila de tablas también planea su vista
    `_vu` y su esquema — doc 87 —, que acá no interesan)."""
    return [c for c in plan.changes if c["collection"] in ("canonical_tables", "canonical_columns")]


def test_r2_recarga_identica_con_fisico_igual_al_derivado_queda_unchanged():
    c = ctx(schemas=_SCHEMAS, tables=[_table(physical="CLIENTE")],
            columns_by_table={"t1": [_col(physical="CODIGO")]})
    plan = build_plan(parsed(tables=[trow(3, "Cliente", physical="CLIENTE", schema="ddv")],
                             columns=[crow(3, "Cliente", "Codigo", physical="CODIGO")]), c)
    assert _model_changes(plan) == []
    assert plan.report["summary"]["tables"] == {"create": 0, "update": 0, "unchanged": 1}
    assert plan.report["summary"]["columns"] == {"create": 0, "update": 0, "unchanged": 1}


def test_r2_recarga_identica_sin_flag_y_fisico_fuera_de_la_regla_queda_unchanged():
    """Dato sin estampar (anterior al doc 68): el flag del planeado es el
    guardado (False) — sin el caso especial de `changed_fields`, sigue igual."""
    t = {**_table(), "physicalNameOverridden": False}
    col = {**_col(), "physicalNameOverridden": False}
    c = ctx(schemas=_SCHEMAS, tables=[t], columns_by_table={"t1": [col]})
    plan = build_plan(parsed(tables=[trow(3, "Cliente", physical="MI_CLIENTE", schema="ddv")],
                             columns=[crow(3, "Cliente", "Codigo", physical="COD_CLI")]), c)
    assert _model_changes(plan) == []
    assert plan.report["summary"]["tables"] == {"create": 0, "update": 0, "unchanged": 1}
    assert plan.report["summary"]["columns"] == {"create": 0, "update": 0, "unchanged": 1}


def test_r2_si_el_fisico_cambia_el_override_se_reevalua():
    """El flag se arrastra SÓLO si el físico no cambia: renombrar (la identidad
    es por físico sin mayúsculas) al físico DERIVADO deja de ser override — el
    estampado del changeset lo baja y el aviso lo nombra."""
    plan = build_plan(parsed(tables=[trow(3, "Cliente", physical="CLIENTE", schema="ddv")]),
                      ctx(schemas=_SCHEMAS, tables=[_table(physical="cliente")]))
    payload = _change(plan, "canonical_tables")["payload"]
    assert payload["physicalName"] == "CLIENTE" and payload["physicalNameOverridden"] is False
    assert _stamped(payload) is False
    assert _message(plan, "existing-table").endswith("(physicalName, physicalNameOverridden).")


def test_r2_changed_fields_no_esconde_el_flag():
    """El caso especial de `changed_fields` (esta misma ronda) escondía un
    cambio REAL del flag con el físico igual — justo el que bajaba el override."""
    before = {"physicalName": "CLIENTE", "physicalNameOverridden": True}
    assert changed_fields(before, {**before, "physicalNameOverridden": False}) == ["physicalNameOverridden"]
