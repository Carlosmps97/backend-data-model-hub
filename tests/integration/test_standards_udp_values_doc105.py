"""Doc 105 (ronda 5) — Data Standards valida los valores de UDP por TIPO antes
de escribir: el default de una definición (`UdpEdit.defaultValue`) y los
valores UDP de un dominio (`DomainEdit.udpValues`, doc 85). Antes se grababa
cualquier texto: un booleano «Sí»/«yes» (el GROUP BY del Reporting los partía
en grupos distintos), un número «nan», una fecha «31/02/2024» o un valor fuera
de la lista — y la carga Excel los escribía en cada entidad nueva.

- booleano: se normaliza a «true»/«false» (las grafías del motor);
- número: finito; fecha: ISO `YYYY-MM-DD` real; lista: dentro de
  `allowedValues` (se graba la grafía de la lista);
- desconocido → 422 con un mensaje que nombra el UDP, ANTES de escribir nada;
- vacío/None sigue permitido."""
from __future__ import annotations

import pytest


def _apply(admin, pid, body, expect=200):
    return admin.call("POST", f"/api/projects/{pid}/standards/apply", body, expect=expect)[1]


def _udp(admin, pid, name) -> dict:
    return next(u for u in admin.get(f"/api/projects/{pid}/udp") if u["name"] == name)


def _state(admin, pid, fake_db) -> tuple:
    return (sorted(fake_db.raw["udp_definitions"].find({}), key=lambda d: d["_id"]),
            sorted(fake_db.raw["parent_domains"].find({}), key=lambda d: d["_id"]),
            sorted(fake_db.raw["glossary_terms"].find({}), key=lambda d: d["_id"]),
            len(admin.get(f"/api/projects/{pid}/standards/versions")))


def _def(name, data_type, default, allowed=None):
    return {"name": name, "level": "column", "dataType": data_type, "defaultValue": default,
            "allowedValues": allowed or []}


# ── Default de la definición ────────────────────────────────────────────────

@pytest.mark.parametrize("data_type, default, allowed, stored", [
    ("boolean", "Sí", None, "true"),
    ("boolean", " NO ", None, "false"),
    ("number", "12.5", None, "12.5"),
    ("date", "2024-02-29", None, "2024-02-29"),
    ("list", "alto", ["Alto", "Bajo"], "Alto"),                    # se graba la grafía de la lista
    ("list", " no  dac ", ["DAC", "No DAC"], "No DAC"),            # espacios de más: como la carga Excel
    ("string", " libre ", None, " libre "),
    ("boolean", "", None, ""),                                      # vacío: permitido
    ("boolean", None, None, None),
])
def test_default_valido_se_graba_normalizado(api, world, data_type, default, allowed, stored):
    admin, pid = api("admin"), world["pid"]
    _apply(admin, pid, {"kind": "udp", "udpUpsert": [_def("Dato", data_type, default, allowed)]})
    assert _udp(admin, pid, "Dato")["defaultValue"] == stored


@pytest.mark.parametrize("data_type, default, allowed", [
    ("boolean", "quizás", None),
    ("number", "nan", None), ("number", "inf", None), ("number", "abc", None),
    ("date", "31/02/2024", None), ("date", "2024-02-30", None), ("date", "hoy", None),
    ("list", "Medio", ["Alto", "Bajo"]),
])
def test_default_invalido_es_422_y_no_escribe_nada(api, world, fake_db, data_type, default, allowed):
    admin, pid = api("admin"), world["pid"]
    before = _state(admin, pid, fake_db)
    out = _apply(admin, pid, {"kind": "batch",
                              "termsUpsert": [{"term": "sucursal", "abbrev": "SUC", "scope": "column"}],
                              "udpUpsert": [_def("Mi Dato", data_type, default, allowed)]}, expect=422)
    assert isinstance(out["detail"], str) and "'Mi Dato'" in out["detail"] and default in out["detail"]
    assert _state(admin, pid, fake_db) == before


def test_editar_una_definicion_a_booleana_normaliza_su_default(api, world):
    admin, pid = api("admin"), world["pid"]
    _apply(admin, pid, {"kind": "udp", "udpUpsert": [_def("Flag", "string", "yes")]})
    udp = _udp(admin, pid, "Flag")
    _apply(admin, pid, {"kind": "udp", "udpUpsert": [{**_def("Flag", "boolean", "yes"), "id": udp["id"]}]})
    assert _udp(admin, pid, "Flag")["defaultValue"] == "true"


def test_repetir_la_grafia_de_un_booleano_ya_normalizado_no_es_un_cambio(api, world):
    admin, pid = api("admin"), world["pid"]
    _apply(admin, pid, {"kind": "udp", "udpUpsert": [_def("Flag", "boolean", "Sí")]})
    udp = _udp(admin, pid, "Flag")
    out = _apply(admin, pid, {"kind": "udp", "udpUpsert": [{**_def("Flag", "boolean", "si"), "id": udp["id"]}]},
                 expect=422)
    assert out["detail"] == "There are no changes to apply."


# ── Valores UDP de un dominio (doc 85) ──────────────────────────────────────

def _defs(admin, pid) -> dict:
    _apply(admin, pid, {"kind": "udp", "udpUpsert": [
        _def("Activo", "boolean", None), _def("Peso", "number", None), _def("Alta", "date", None),
        _def("Nivel", "list", None, ["Alto", "Bajo"]), _def("Nota", "string", None)]})
    return {u["name"]: u["id"] for u in admin.get(f"/api/projects/{pid}/udp")}


def _domain(admin, pid, name="Fecha") -> dict:
    return next(d for d in admin.get(f"/api/projects/{pid}/domains") if d["name"] == name)


def test_valores_udp_del_dominio_validos_se_graban_normalizados(api, world):
    admin, pid = api("admin"), world["pid"]
    ids = _defs(admin, pid)
    _apply(admin, pid, {"kind": "domain", "domainsUpsert": [{"name": "Fecha", "defaultDataType": "DATE", "udpValues": {
        ids["Activo"]: "Sí", ids["Peso"]: "7", ids["Alta"]: "2024-01-31", ids["Nivel"]: "bajo",
        ids["Nota"]: " libre ", "udp-que-no-existe": "x", ids["Activo"] + "-vacio": ""}}]})
    assert _domain(admin, pid)["udpValues"] == {
        ids["Activo"]: "true", ids["Peso"]: "7", ids["Alta"]: "2024-01-31", ids["Nivel"]: "Bajo",
        ids["Nota"]: " libre ", "udp-que-no-existe": "x", ids["Activo"] + "-vacio": ""}


@pytest.mark.parametrize("udp_name, value", [("Activo", "quizás"), ("Peso", "nan"), ("Alta", "31/02/2024"),
                                             ("Nivel", "Medio")])
def test_valor_udp_del_dominio_invalido_es_422_y_no_escribe_nada(api, world, fake_db, udp_name, value):
    admin, pid = api("admin"), world["pid"]
    ids = _defs(admin, pid)
    before = _state(admin, pid, fake_db)
    out = _apply(admin, pid, {"kind": "domain", "domainsUpsert": [
        {"name": "Fecha", "defaultDataType": "DATE", "udpValues": {ids[udp_name]: value}}]}, expect=422)
    assert f"'{udp_name}'" in out["detail"] and "'Fecha'" in out["detail"] and value in out["detail"]
    assert _state(admin, pid, fake_db) == before


def test_el_dominio_valida_contra_la_definicion_editada_en_el_mismo_lote(api, world):
    """La definición cambia de tipo en el MISMO lote: rige el tipo nuevo."""
    admin, pid = api("admin"), world["pid"]
    ids = _defs(admin, pid)
    _apply(admin, pid, {"kind": "batch",
                        "udpUpsert": [{**_def("Nota", "boolean", None), "id": ids["Nota"]}],
                        "domainsUpsert": [{"name": "Fecha", "defaultDataType": "DATE",
                                           "udpValues": {ids["Nota"]: "yes"}}]})
    assert _domain(admin, pid)["udpValues"] == {ids["Nota"]: "true"}


# ── Ronda 6 (R16/S1-S2): sólo se validan los valores que CAMBIAN ──────────
# El front manda TODOS los `udpValues` del dominio aunque se edite sólo la
# descripción: un valor ya guardado que dejó de valer (se sacó de la lista, o
# la definición cambió de tipo) bloqueaba cualquier edición del dominio.

def _domain_edit(dom: dict, **changes) -> dict:
    base = {"id": dom["id"], "name": dom["name"], "defaultDataType": dom["defaultDataType"],
            "description": dom.get("description"), "physicalName": dom.get("physicalName") or "",
            "physicalDescription": dom.get("physicalDescription") or "",
            "udpValues": dom.get("udpValues") or {}, "inheritsName": bool(dom.get("inheritsName"))}
    return {**base, **changes}


def _seed_domain(admin, pid, data_type, allowed, value):
    _apply(admin, pid, {"kind": "udp", "udpUpsert": [_def("Clasif", data_type, None, allowed)]})
    udp = _udp(admin, pid, "Clasif")
    _apply(admin, pid, {"kind": "domain", "domainsUpsert": [
        {"name": "Monto", "defaultDataType": "DECIMAL(18,2)", "udpValues": {udp["id"]: value}}]})
    return udp, _domain(admin, pid, "Monto")


@pytest.mark.parametrize("data_type, allowed, value, new_def", [
    ("list", ["A", "B"], "A", {"dataType": "list", "allowedValues": ["B", "C"]}),     # S1: se sacó «A»
    ("string", None, "alto", {"dataType": "number"}),                                   # S2: cambió el tipo
])
def test_r16_un_valor_ya_guardado_no_bloquea_editar_el_dominio(api, world, data_type, allowed, value, new_def):
    admin, pid = api("admin"), world["pid"]
    udp, dom = _seed_domain(admin, pid, data_type, allowed, value)
    _apply(admin, pid, {"kind": "udp", "udpUpsert": [{**_def("Clasif", data_type, None, allowed), **new_def,
                                                      "id": udp["id"]}]})
    _apply(admin, pid, {"kind": "domain", "domainsUpsert": [_domain_edit(dom, description="nueva")]})
    after = _domain(admin, pid, "Monto")
    assert (after["description"], after["udpValues"]) == ("nueva", {udp["id"]: value})


def test_r16_cambiar_ese_valor_por_otro_invalido_sigue_siendo_422(api, world):
    admin, pid = api("admin"), world["pid"]
    udp, dom = _seed_domain(admin, pid, "string", None, "alto")
    _apply(admin, pid, {"kind": "udp", "udpUpsert": [{**_def("Clasif", "number", None), "id": udp["id"]}]})
    out = _apply(admin, pid, {"kind": "domain", "domainsUpsert": [
        _domain_edit(dom, udpValues={udp["id"]: "bajo"})]}, expect=422)
    assert "'bajo'" in out["detail"] and "'Clasif'" in out["detail"]


# ── Ronda 6 (R16/H2, B1): número canónico y fecha-hora → fecha ─────────────

@pytest.mark.parametrize("data_type, default, stored", [
    ("number", "10.50", "10.5"), ("number", "1e3", "1000"), ("number", "007", "7"),
    ("date", "2024-01-15 10:30", "2024-01-15"), ("date", "2024-01-15T08:00:59", "2024-01-15"),
])
def test_r16_default_numero_canonico_y_fecha_hora_a_fecha(api, world, data_type, default, stored):
    admin, pid = api("admin"), world["pid"]
    _apply(admin, pid, {"kind": "udp", "udpUpsert": [_def("Dato", data_type, default)]})
    assert _udp(admin, pid, "Dato")["defaultValue"] == stored


def test_r16_valores_de_dominio_numero_canonico_y_fecha_hora_a_fecha(api, world):
    admin, pid = api("admin"), world["pid"]
    ids = _defs(admin, pid)
    _apply(admin, pid, {"kind": "domain", "domainsUpsert": [{"name": "Fecha", "defaultDataType": "DATE",
                                                             "udpValues": {ids["Peso"]: "10.50",
                                                                           ids["Alta"]: "2024-01-15T08:00"}}]})
    assert _domain(admin, pid)["udpValues"] == {ids["Peso"]: "10.5", ids["Alta"]: "2024-01-15"}
