"""Doc 105 (ronda 5, decisión del coordinador) — carga Excel: UDP de número y
de fecha.

- Número: sólo FINITO — `float()` aceptaba «nan» e «inf» y se grababan. Ronda
  6 (R16/H2): se graba en su forma CANÓNICA (`canonical_number`, la del motor
  del Reporting): «10.50» y «10.5» deben ser el mismo valor para una consulta.
- Fecha: ISO `YYYY-MM-DD` y fecha real — no se validaba nada («31/02/2024»,
  «1/2/24» o «hoy» se grababan). El front manda las celdas de fecha así; otra
  forma de texto es un error de la fila. Ronda 6 (R16/B1): una celda con hora
  llega «YYYY-MM-DD HH:MM[:SS]» y se graba sólo la fecha.

Una celda inválida es `invalid-udp-value` (como un valor fuera de la lista);
el default de la definición pasa por la misma regla. Puro."""
from __future__ import annotations

import pytest

from app.features.bulk_upload.planner import build_plan

from .helpers import by_coll, ctx, parsed, trow

_SCHEMAS = [{"id": "s1", "name": "ddv", "kind": "tables"}]


def _def(data_type, default=None):
    return {"id": "u", "name": "Dato", "level": "table", "dataType": data_type, "defaultValue": default,
            "allowedValues": []}


def _plan(defn, **row_kw):
    return build_plan(parsed(tables=[trow(3, "A", schema="ddv", **row_kw)], table_udp={"UDP_Dato": [defn]}),
                      ctx(schemas=_SCHEMAS, udp_defs=[defn]))


def _udp(plan) -> dict:
    (ch,) = by_coll(plan)["canonical_tables"]
    return ch["payload"]["udpValues"]


@pytest.mark.parametrize("raw", ["nan", "NaN", "inf", "-inf", "Infinity", "1e400"])
def test_numero_no_finito_es_invalid_udp_value(raw):
    plan = _plan(_def("number"), udp={"UDP_Dato": raw})
    (e,) = plan.report["errors"]
    assert (e["code"], e["row"], e["column"]) == ("invalid-udp-value", 3, "UDP_Dato") and raw in e["message"]


@pytest.mark.parametrize("raw, stored", [("12.5", "12.5"), ("10.50", "10.5"), ("10.0", "10"), ("1e3", "1000"),
                                         ("-7", "-7"), ("007", "7")])
def test_numero_valido_se_graba_en_su_forma_canonica(raw, stored):
    assert _udp(_plan(_def("number"), udp={"UDP_Dato": raw})) == {"u": stored}


@pytest.mark.parametrize("raw", ["31/02/2024", "2024-02-30", "1/2/24", "2024-2-3", "hoy", "2024-01-15 25:00",
                                 "2024-01-15T10:30Z"])
def test_fecha_que_no_es_iso_real_es_invalid_udp_value(raw):
    plan = _plan(_def("date"), udp={"UDP_Dato": raw})
    (e,) = plan.report["errors"]
    assert (e["code"], e["row"], e["column"]) == ("invalid-udp-value", 3, "UDP_Dato")
    assert raw in e["message"] and "YYYY-MM-DD" in e["message"]


@pytest.mark.parametrize("raw", ["2024-02-29", "2024-02-29 10:30", "2024-02-29T23:59:59"])
def test_fecha_o_fecha_hora_iso_valida_graba_la_fecha(raw):
    assert _udp(_plan(_def("date"), udp={"UDP_Dato": raw})) == {"u": "2024-02-29"}


@pytest.mark.parametrize("data_type, default", [("number", "nan"), ("date", "31/12/2026")])
def test_el_default_invalido_de_la_definicion_es_error_de_la_fila(data_type, default):
    plan = _plan(_def(data_type, default))
    (e,) = plan.report["errors"]
    assert e["code"] == "invalid-udp-value" and "default" in e["message"] and default in e["message"]


# ── Ronda 6 (R16/B2): el default del PERFIL de carga ───────────────────────

@pytest.mark.parametrize("data_type, default", [("date", "31/12/2026"), ("number", "nan"), ("boolean", "quizás")])
def test_default_invalido_del_perfil_dice_que_viene_del_perfil(data_type, default):
    plan = _plan(_def(data_type), udp={"UDP_Dato": ""}, udp_defaults={"UDP_Dato": default})
    (e,) = plan.report["errors"]
    assert e["code"] == "invalid-udp-value" and (e["row"], e["column"]) == (3, "UDP_Dato")
    assert "default" in e["message"] and "upload profile" in e["message"] and default in e["message"]


def test_la_celda_invalida_no_se_atribuye_al_perfil():
    plan = _plan(_def("date"), udp={"UDP_Dato": "31/12/2026"}, udp_defaults={"UDP_Dato": "2026-12-31"})
    (e,) = plan.report["errors"]
    assert "profile" not in e["message"]


def test_default_valido_del_perfil_se_normaliza():
    assert _udp(_plan(_def("number"), udp={"UDP_Dato": ""}, udp_defaults={"UDP_Dato": "1e3"})) == {"u": "1000"}
