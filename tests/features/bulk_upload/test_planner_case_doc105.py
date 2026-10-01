"""Doc 105 — el planner modela el `case` del físico TIPEADO como el changeset.

El changeset normaliza al `case` del scope el `physicalName` de toda columna
que se graba (doc 83, `_apply_naming_rules`, `_CASE_NORMALIZED`) ANTES de la
unicidad, la longitud y el estampado del override; las tablas entran tal cual
(decisión owner 2026-09-10). El planner comparaba el físico tipeado crudo: una
columna que en la hoja sólo difería en mayúsculas/minúsculas salía «updated
(physicalName…)» con aviso de renombre y el override re-evaluado, aunque lo
grabado quedaba igual. Ahora identidad, validaciones, comparación y payload
ven el nombre que se persistirá. Ronda 4: el changeset ya no renombra un físico
LEGADO que el cambio repite tal cual — el planner tampoco. Puro."""
from __future__ import annotations

from app.features.bulk_upload.planner import build_plan

from .helpers import by_coll, codes, crow, ctx, naming, parsed, seq_ids, trow

_SCHEMAS = [{"id": "s1", "name": "ddv", "kind": "tables"}]


def _table(tid="t1", physical="CLIENTE", logical="Cliente"):
    return {"id": tid, "projectId": "p1", "physicalName": physical, "logicalName": logical, "schema": "ddv",
            "description": None, "udpValues": {}}


def _col(cid, physical, logical, ordinal=0, **kw):
    doc = {"id": cid, "projectId": "p1", "tableId": "t1", "physicalName": physical, "logicalName": logical,
           "parentDomainId": None, "dataType": "STRING", "typeOverridden": False, "isPrimaryKey": None,
           "isForeignKey": None, "isNullable": True, "isPartition": False, "description": None,
           "ordinal": ordinal, "udpValues": {}}
    doc.update(kw)
    return doc


def _ctx(cols, case="upper", max_len=150):
    n = naming(max_len)
    n["column"]["case"] = case
    return ctx(schemas=_SCHEMAS, naming=n, tables=[_table()], columns_by_table={"t1": cols})


def _cols(plan):
    return by_coll(plan).get("canonical_columns", [])


def _message(plan, code):
    return next(w["message"] for w in plan.report["warnings"] if w["code"] == code)


# ── Sólo mayúsculas/minúsculas: lo grabado no cambia → «unchanged» ─────────

def test_columna_que_solo_difiere_en_mayusculas_queda_unchanged():
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Codigo", physical="cod")]), _ctx([_col("c1", "COD", "Codigo")]))
    assert _cols(plan) == [] and plan.report["warnings"] == []
    assert plan.report["summary"]["columns"] == {"create": 0, "update": 0, "unchanged": 1}


def test_solo_mayusculas_con_otro_cambio_no_renombra_ni_toca_el_override():
    """Override con físico igual al derivado: el flag se conserva (físico sin
    cambio real) y el aviso nombra sólo la descripción."""
    col = _col("c1", "CODIGO", "Codigo", physicalNameOverridden=True)
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Codigo", physical="Codigo", description="d")]), _ctx([col]))
    (ch,) = _cols(plan)
    assert (ch["payload"]["physicalName"], ch["payload"]["physicalNameOverridden"]) == ("CODIGO", True)
    assert codes(plan, "warning") == ["existing-column"]
    assert _message(plan, "existing-column").endswith("it will be updated (description).")


def test_columna_nueva_tipeada_en_minusculas_nace_con_la_regla():
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Codigo Nuevo", physical="cod_nuevo", data_type="STRING")]),
                      _ctx([]), new_id=seq_ids())
    (ch,) = _cols(plan)
    assert ch["payload"]["physicalName"] == "COD_NUEVO"


def test_la_vista_vu_referencia_el_fisico_que_se_graba():
    p = parsed(tables=[trow(3, "Venta", schema="ddv")],
               columns=[crow(3, "Venta", "Codigo Venta", physical="cod_venta", data_type="STRING", pk=True)])
    plan = build_plan(p, ctx(schemas=_SCHEMAS), new_id=seq_ids())
    (view,) = [c["payload"] for c in by_coll(plan)["views"]]
    assert [s["column"] for s in view["sources"]] == ["COD_VENTA"]


def test_dos_filas_que_se_grabarian_igual_son_duplicado_en_el_archivo():
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Codigo Uno", physical="cod_x", data_type="STRING"),
                                      crow(4, "Cliente", "Codigo Dos", physical="COD_X", data_type="STRING")]),
                      _ctx([]), new_id=seq_ids())
    assert codes(plan, "error") == ["duplicate-in-file"]


# ── camel: con separadores se re-segmenta; sin separadores queda tal cual ──

def test_camel_con_separadores_calza_con_la_columna_ya_normalizada():
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Codigo Cliente", physical="cod_cliente")]),
                      _ctx([_col("c1", "codCliente", "Codigo Cliente")], case="camel"))
    assert _cols(plan) == [] and plan.report["summary"]["columns"] == {"create": 0, "update": 0, "unchanged": 1}


def test_camel_la_longitud_se_mide_sobre_el_nombre_que_se_graba():
    # «co_cli_nue» (10) se graba «coCliNue» (8): dentro del tope de 9.
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Codigo", physical="co_cli_nue", data_type="STRING")]),
                      _ctx([], case="camel", max_len=9), new_id=seq_ids())
    assert plan.report["errors"] == []
    (ch,) = _cols(plan)
    assert ch["payload"]["physicalName"] == "coCliNue"


def test_camel_sin_separadores_queda_tal_cual():
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Codigo Nuevo", physical="CodNuevo", data_type="STRING")]),
                      _ctx([], case="camel"), new_id=seq_ids())
    (ch,) = _cols(plan)
    assert ch["payload"]["physicalName"] == "CodNuevo"


# ── Lo que NO cambia ────────────────────────────────────────────────────────

def test_recarga_identica_queda_unchanged():
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Codigo", physical="COD")]), _ctx([_col("c1", "COD", "Codigo")]))
    assert _cols(plan) == [] and plan.report["warnings"] == []


def test_recarga_identica_de_un_nombre_legado_fuera_de_la_regla_queda_unchanged():
    """Columna grabada antes del doc 83 (minúsculas): la re-carga con el mismo
    texto no la renombra."""
    for case, stored in (("upper", "cod"), ("camel", "cod_cliente")):
        plan = build_plan(parsed(columns=[crow(3, "Cliente", "Codigo", physical=stored)]),
                          _ctx([_col("c1", stored, "Codigo")], case=case))
        assert _cols(plan) == [], case
        assert plan.report["summary"]["columns"] == {"create": 0, "update": 0, "unchanged": 1}, case


def test_un_cambio_real_de_fisico_sigue_igual_que_hoy():
    # Legado «cod» y la hoja trae «COD»: lo grabado SÍ cambia (cod → COD).
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Codigo", physical="COD")]), _ctx([_col("c1", "cod", "Codigo")]))
    (ch,) = _cols(plan)
    assert ch["payload"]["physicalName"] == "COD" and "rename" in codes(plan, "warning")
    # camel sin separadores: «CodCliente» se graba tal cual → renombre de «codCliente».
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Codigo Cliente", physical="CodCliente")]),
                      _ctx([_col("c1", "codCliente", "Codigo Cliente")], case="camel"))
    (ch,) = _cols(plan)
    assert ch["payload"]["physicalName"] == "CodCliente" and "rename" in codes(plan, "warning")
    # Otro físico: identidad por físico → columna NUEVA (como hoy).
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Codigo", physical="OTRO", data_type="STRING")]),
                      _ctx([_col("c1", "COD", "Codigo")]), new_id=seq_ids())
    assert plan.report["summary"]["columns"] == {"create": 1, "update": 0, "unchanged": 0}


def test_las_tablas_conservan_la_grafia_tipeada_como_el_changeset():
    plan = build_plan(parsed(tables=[trow(3, "Cliente", physical="cliente", schema="ddv")]),
                      ctx(schemas=_SCHEMAS, tables=[_table()]))
    (ch,) = by_coll(plan)["canonical_tables"]
    assert ch["payload"]["physicalName"] == "cliente" and "rename" in codes(plan, "warning")


def test_el_planner_normaliza_los_mismos_scopes_que_el_changeset():
    """Contrato: si el changeset extiende la normalización (p. ej. a tablas),
    el planner debe seguirlo — si no, vuelve a reportar cambios que no son."""
    from app.features.bulk_upload.plan_columns import CASE_NORMALIZED_SCOPES
    from app.features.changesets.service import _CASE_NORMALIZED, _OVERRIDE_SCOPES
    assert {_OVERRIDE_SCOPES[c] for c in _CASE_NORMALIZED} == set(CASE_NORMALIZED_SCOPES)


# ── Nombre LEGADO (fuera de la regla) y una fila con otro cambio ───────────
# Ronda 4: el changeset aplica la regla de case a lo que se TIPEA o cambia; un
# físico legado que el cambio repite tal cual queda tal cual (renombrarlo en
# silencio dejaba vistas colgando, cambiaba el override y podía chocar con otra
# columna). El planner hace lo mismo: payload con el nombre grabado, sin aviso.

from app.features.glossary.service import is_physical_override  # noqa: E402


def test_legado_tipeado_identico_con_otro_cambio_queda_tal_cual():
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Codigo", physical="cod", description="d")]),
                      _ctx([_col("c1", "cod", "Codigo")]))
    (ch,) = _cols(plan)
    assert ch["payload"]["physicalName"] == "cod"
    assert codes(plan, "warning") == ["existing-column"]
    assert _message(plan, "existing-column").endswith("it will be updated (description).")


def test_legado_por_logico_queda_tal_cual_con_o_sin_otro_cambio():
    col = _col("c1", "nbr_cli", "Nombre Cliente")                   # el derivado sería NOMBRECLIENTE
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Nombre Cliente", description="d")]), _ctx([col]))
    (ch,) = _cols(plan)
    assert ch["payload"]["physicalName"] == "nbr_cli" and "rename" not in codes(plan, "warning")
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Nombre Cliente")]), _ctx([col]))
    assert _cols(plan) == [] and plan.report["summary"]["columns"]["unchanged"] == 1


def test_el_legado_intacto_conserva_su_override():
    """Mismo nombre ⇒ el flag del existente viaja (hallazgo 4) y el estampado lo
    conserva — como la misma edición hecha desde la app."""
    col = _col("c1", "cod", "Cod", physicalNameOverridden=True)
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Cod", physical="cod", description="d")]), _ctx([col]))
    (ch,) = _cols(plan)
    assert (ch["payload"]["physicalName"], ch["payload"]["physicalNameOverridden"]) == ("cod", True)
    assert is_physical_override(ch["payload"], {}, "", "upper") is True


def test_la_longitud_no_penaliza_lo_que_el_changeset_ve_heredado():
    """El «grandfather» del changeset compara casefold: «nbrcliente_x» → «NBRCLIENTE_X»
    (12 > 10) no es un renombre para la longitud."""
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Nombre", physical="NBRCLIENTE_X", description="d")]),
                      _ctx([_col("c1", "nbrcliente_x", "Nombre")], max_len=10))
    assert plan.report["errors"] == []
    (ch,) = _cols(plan)
    assert ch["payload"]["physicalName"] == "NBRCLIENTE_X"


def test_nombre_tipeado_que_al_grabarse_choca_con_otra_columna_es_duplicado():
    """camel: «NBR_CLIENTE» (renombra la legada «nbr_cliente») se grabaría
    «nbrCliente», que choca sin mirar mayúsculas con la columna «nbrcliente»
    que la carga no toca: el changeset lo rechazaría (409) al aplicar."""
    cols = [_col("c1", "nbr_cliente", "Nombre"), _col("c2", "nbrcliente", "Nombre Corto", 1)]
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Nombre", physical="NBR_CLIENTE")]),
                      _ctx(cols, case="camel"))
    assert [(e["code"], e["row"]) for e in plan.report["errors"]] == [("duplicate-name", 3)]
    assert "nbrCliente" in plan.report["errors"][0]["message"] and _cols(plan) == []
    # Repetir el legado tal cual no choca: queda como está.
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Nombre", physical="nbr_cliente", description="d")]),
                      _ctx(cols, case="camel"))
    assert plan.report["errors"] == [] and _cols(plan)[0]["payload"]["physicalName"] == "nbr_cliente"


def test_camel_dos_filas_que_se_grabarian_igual_son_duplicado_en_el_archivo():
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Nombre Uno", physical="nbr_x", data_type="STRING"),
                                      crow(4, "Cliente", "Nombre Dos", physical="nbrX", data_type="STRING")]),
                      _ctx([], case="camel"), new_id=seq_ids())
    assert [(e["code"], e["row"]) for e in plan.report["errors"]] == [("duplicate-in-file", 4)]


# ── Ronda 5 (R13): SIN físico declarado la fila no pide un nombre ─────────
# Se pedía el DERIVADO: si el legado difería sólo en mayúsculas, lo encontraba
# la identidad (sin mirar mayúsculas) y se grababa el derivado con aviso de
# renombre. La misma edición desde la app lo conserva.

def test_sin_fisico_declarado_la_columna_legada_conserva_su_grafia():
    col = _col("c1", "nombrecliente", "Nombre Cliente")                   # derivado: NOMBRECLIENTE
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Nombre Cliente", description="d")]), _ctx([col]))
    (ch,) = _cols(plan)
    assert ch["payload"]["physicalName"] == "nombrecliente"
    assert codes(plan, "warning") == ["existing-column"]
    plan = build_plan(parsed(columns=[crow(3, "Cliente", "Nombre Cliente")]), _ctx([col]))
    assert _cols(plan) == [] and plan.report["summary"]["columns"]["unchanged"] == 1


def test_sin_fisico_declarado_la_tabla_legada_conserva_su_grafia():
    plan = build_plan(parsed(tables=[trow(3, "Cliente", schema="ddv", description="Def")]),
                      ctx(schemas=_SCHEMAS, tables=[_table(physical="cliente")]))
    (ch,) = by_coll(plan)["canonical_tables"]
    assert ch["payload"]["physicalName"] == "cliente" and "rename" not in codes(plan, "warning")


# ── Tablas: el tope de longitud con el «grandfather» del changeset ─────────

def test_tabla_legada_larga_renombrada_solo_en_mayusculas_no_es_name_too_long():
    """El changeset (alta suelta y lote) no penaliza el nombre ACTUAL sin mirar
    mayúsculas: «cliente_largo_x» → «CLIENTE_LARGO_X» (15 > 10) se graba."""
    plan = build_plan(parsed(tables=[trow(3, "Cliente", physical="CLIENTE_LARGO_X", schema="ddv")]),
                      ctx(schemas=_SCHEMAS, naming=naming(10), tables=[_table(physical="cliente_largo_x")]))
    assert plan.report["errors"] == []
    (ch,) = by_coll(plan)["canonical_tables"]
    assert ch["payload"]["physicalName"] == "CLIENTE_LARGO_X" and "rename" in codes(plan, "warning")


def test_tabla_nueva_larga_sigue_siendo_name_too_long():
    plan = build_plan(parsed(tables=[trow(3, "Otra", physical="OTRA_TABLA_LARGA", schema="ddv")]),
                      ctx(schemas=_SCHEMAS, naming=naming(10), tables=[_table(physical="cliente_largo_x")]))
    assert [e["code"] for e in plan.report["errors"]] == ["name-too-long"]
