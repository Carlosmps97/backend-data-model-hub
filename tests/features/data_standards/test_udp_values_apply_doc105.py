"""Doc 105 (ronda 5) — `checked_udp_values` (puro): los valores UDP de un
dominio se validan contra las definiciones DESPUÉS del lote. La que el mismo
lote borra ya no rige: su valor pasa tal cual (el guard de borrado de UDP en
uso decide aparte)."""
from __future__ import annotations

from app.features.data_standards.schemas import ApplyBody
from app.features.data_standards.service import checked_udp_values

BEFORE = {"u1": {"id": "u1", "name": "Activo", "dataType": "boolean", "allowedValues": [], "defaultValue": None}}


def _body(**kw) -> ApplyBody:
    return ApplyBody(domainsUpsert=[{"name": "Fecha", "defaultDataType": "DATE", "udpValues": {"u1": "quizás"}}],
                     **kw)


def test_el_valor_de_una_definicion_borrada_en_el_lote_pasa_tal_cual():
    body, err = checked_udp_values(_body(udpDelete=["u1"]), BEFORE)
    assert err is None and body.domainsUpsert[0].udpValues == {"u1": "quizás"}


def test_sin_borrarla_el_mismo_valor_es_invalido():
    _body_out, err = checked_udp_values(_body(), BEFORE)
    assert err == "The value 'quizás' of UDP 'Activo' in domain 'Fecha' is not a boolean (use true or false)."
