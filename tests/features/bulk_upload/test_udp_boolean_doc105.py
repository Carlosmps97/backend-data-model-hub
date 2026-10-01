"""Doc 105 (ronda 5) — carga Excel: los UDP booleanos se escriben «true»/«false».

La celda ya se normalizaba (y una grafía desconocida es `invalid-udp-value`,
como un valor fuera de la lista en un UDP de lista), pero el DEFAULT de la
definición (Data Standards lo guarda como texto libre: «Sí», «yes»…) se
escribía crudo en la entidad nueva: el GROUP BY del Reporting partía un mismo
valor en varios grupos. Ahora pasa por la misma normalización
(`app.core.udp_values.normalize_boolean`, las grafías del motor). Puro."""
from __future__ import annotations

from app.features.bulk_upload.planner import build_plan

from .helpers import by_coll, ctx, parsed, trow

_SCHEMAS = [{"id": "s1", "name": "ddv", "kind": "tables"}]


def _def(default, data_type="boolean", did="b"):
    return {"id": did, "name": "Activo", "level": "table", "dataType": data_type, "defaultValue": default,
            "allowedValues": []}


def _plan(defs, **row_kw):
    return build_plan(parsed(tables=[trow(3, "A", schema="ddv", **row_kw)], table_udp={"UDP_Activo": defs}),
                      ctx(schemas=_SCHEMAS, udp_defs=defs))


def _udp(plan) -> dict:
    (ch,) = by_coll(plan)["canonical_tables"]
    return ch["payload"]["udpValues"]


def test_el_default_booleano_de_la_definicion_se_escribe_normalizado():
    for default, expected in (("Sí", "true"), (" YES ", "true"), ("1", "true"), ("No", "false"), ("falso", "false")):
        plan = _plan([_def(default)])
        assert plan.report["errors"] == [] and _udp(plan) == {"b": expected}, default


def test_un_default_booleano_desconocido_es_invalid_udp_value_como_en_una_lista():
    plan = _plan([_def("quizás")])
    (e,) = plan.report["errors"]
    assert (e["code"], e["row"], e["column"]) == ("invalid-udp-value", 3, "UDP_Activo")
    assert "quizás" in e["message"] and "default" in e["message"]
    assert "canonical_tables" not in by_coll(plan)


def test_la_celda_y_el_default_del_perfil_siguen_normalizandose():
    assert _udp(_plan([_def(None)], udp={"UDP_Activo": " sí "})) == {"b": "true"}
    assert _udp(_plan([_def(None)], udp_defaults={"UDP_Activo": "NO"})) == {"b": "false"}
    plan = _plan([_def(None)], udp={"UDP_Activo": "quizás"})
    assert [e["code"] for e in plan.report["errors"]] == ["invalid-udp-value"]


def test_los_defaults_validos_de_texto_numero_y_fecha():
    """Texto y fecha, tal cual; número, en su forma canónica (ronda 6, R16/H2)."""
    for data_type, default, stored in (("string", " Algo ", "Algo"), ("number", "10.0", "10"),
                                       ("date", "2026-12-31", "2026-12-31")):
        plan = _plan([_def(default, data_type=data_type)])
        assert _udp(plan) == {"b": stored}, data_type
