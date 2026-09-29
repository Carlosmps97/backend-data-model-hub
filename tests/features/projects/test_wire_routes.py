"""Trazos manuales de wires (doc 99): `routes` del canvas — puntos de quiebre
por wire, en coordenadas del canvas.

Dos reglas: la LECTURA es tolerante (`clean_routes`: un trazo corrupto se
descarta y el canvas abre igual — es decoración) y la ESCRITURA es estricta
(`routes_error`: un cliente que manda basura se entera con un 422)."""
from __future__ import annotations

import pytest

from app.features.projects.models import (
    MAX_ROUTE_COORD,
    MAX_ROUTE_ID_LEN,
    MAX_ROUTE_POINTS,
    MAX_ROUTES,
    SubjectAreaDoc,
    clean_routes,
    routes_error,
)


def _sa(**over) -> dict:
    return {"id": "sa1", "projectId": "p1", "name": "Canvas", **over}


# ── modelo: round-trip ─────────────────────────────────────────────────────


def test_canvas_sin_routes_nace_con_el_mapa_vacio():
    assert SubjectAreaDoc.model_validate(_sa()).model_dump()["routes"] == {}


def test_routes_hace_round_trip_por_el_modelo():
    routes = {"rel-1": [{"x": 420, "y": 180}, {"x": 420.5, "y": 96}],
              "subsym-s1": [{"x": -12, "y": 0}]}
    out = SubjectAreaDoc.model_validate(_sa(routes=routes)).model_dump()["routes"]
    assert out == routes


def test_routes_no_toca_los_demas_campos_del_canvas():
    doc = SubjectAreaDoc.model_validate(_sa(
        tableIds=["t1"], layout={"t1": {"x": 1, "y": 2}}, drawings=[{"id": "d1"}],
        viewIds=None, routes={"r": [{"x": 5, "y": 6}]})).model_dump()
    assert doc["tableIds"] == ["t1"]
    assert doc["layout"] == {"t1": {"x": 1, "y": 2}}
    assert doc["drawings"] == [{"id": "d1"}]
    assert doc["viewIds"] is None


# ── lectura tolerante ──────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", [None, "x", 5, True, [], [{"x": 1, "y": 2}]])
def test_routes_que_no_es_un_mapa_se_lee_como_vacio(raw):
    assert clean_routes(raw) == {}
    assert SubjectAreaDoc.model_validate(_sa(routes=raw)).routes == {}


def test_un_trazo_malformado_se_descarta_entero_y_los_sanos_quedan():
    raw = {
        "ok": [{"x": 1, "y": 2}, {"x": 3.5, "y": 4}],
        "no-es-lista": "soy texto",
        "punto-texto": [{"x": 1, "y": 2}, {"x": "a", "y": 2}],
        "punto-incompleto": [{"y": 9}],
        "punto-nulo": [None],
        "punto-nan": [{"x": float("nan"), "y": 1}],
        "punto-inf": [{"x": 1, "y": float("inf")}],
        "punto-bool": [{"x": True, "y": 1}],
        "fuera-de-rango": [{"x": MAX_ROUTE_COORD + 1, "y": 1}],
        "demasiados": [{"x": i, "y": i} for i in range(MAX_ROUTE_POINTS + 1)],
        "vacio": [],
        "": [{"x": 1, "y": 2}],
    }
    assert clean_routes(raw) == {"ok": [{"x": 1, "y": 2}, {"x": 3.5, "y": 4}]}


def test_la_lectura_solo_conserva_x_e_y_de_cada_punto():
    assert clean_routes({"r": [{"x": 1, "y": 2, "z": 3, "kind": "bend"}]}) == {"r": [{"x": 1, "y": 2}]}


def test_un_canvas_con_trazo_corrupto_valida_igual():
    doc = SubjectAreaDoc.model_validate(_sa(
        layout={"t1": {"x": 1, "y": 2}},
        routes={"ok": [{"x": 1, "y": 2}], "roto": [{"x": "a"}]}))
    assert doc.model_dump()["routes"] == {"ok": [{"x": 1, "y": 2}]}
    assert doc.model_dump()["layout"] == {"t1": {"x": 1, "y": 2}}


def test_clean_routes_no_muta_la_entrada():
    raw = {"r": [{"x": 1, "y": 2, "z": 3}], "roto": "x"}
    clean_routes(raw)
    assert raw == {"r": [{"x": 1, "y": 2, "z": 3}], "roto": "x"}


def test_el_tope_de_puntos_es_inclusivo():
    exact = [{"x": i, "y": i} for i in range(MAX_ROUTE_POINTS)]
    assert clean_routes({"r": exact}) == {"r": exact}
    assert routes_error({"r": exact}) is None


# ── escritura estricta ─────────────────────────────────────────────────────


@pytest.mark.parametrize("raw", [
    None,                                          # llave ausente / nula = sin trazos
    {},
    {"r": []},                                     # lista vacía = sin trazo
    {"r": [{"x": 1, "y": 2.5}]},
    {"r": [{"x": -300, "y": 0}], "subsym-s1": [{"x": 1, "y": 2}, {"x": 3, "y": 4}]},
    {"r": [{"x": MAX_ROUTE_COORD, "y": -MAX_ROUTE_COORD}]},
])
def test_routes_error_acepta_lo_valido(raw):
    assert routes_error(raw) is None


@pytest.mark.parametrize("raw, fragment", [
    ("x", "must be an object"),
    ([{"x": 1, "y": 2}], "must be an object"),
    (5, "must be an object"),
    ({"r": "x"}, "must be a list"),
    ({"r": {"x": 1, "y": 2}}, "must be a list"),
    ({"": [{"x": 1, "y": 2}]}, "wire id"),
    ({"r": [5]}, "must be an object with x and y"),
    ({"r": [None]}, "must be an object with x and y"),
    ({"r": [{"y": 2}]}, "finite number"),
    ({"r": [{"x": "1", "y": 2}]}, "finite number"),
    ({"r": [{"x": None, "y": 2}]}, "finite number"),
    ({"r": [{"x": True, "y": 1}]}, "finite number"),
    ({"r": [{"x": float("nan"), "y": 1}]}, "finite number"),
    ({"r": [{"x": 1, "y": float("-inf")}]}, "finite number"),
    ({"r": [{"x": MAX_ROUTE_COORD + 1, "y": 1}]}, "out of range"),
    ({"r": [{"x": 1, "y": 2}] * (MAX_ROUTE_POINTS + 1)}, "at most"),
])
def test_routes_error_rechaza_lo_malformado(raw, fragment):
    err = routes_error(raw)
    assert err is not None and fragment in err, err


def test_routes_error_nombra_el_wire_y_el_punto():
    err = routes_error({"ok": [{"x": 1, "y": 2}], "rel-9": [{"x": 1, "y": 2}, {"x": "a", "y": 2}]})
    assert err is not None and "rel-9" in err and "[1]" in err


# ── Hallazgos de la revisión independiente (2026-09-28) ─────────────────────

GIGANTE = 10 ** 400        # JSON admite enteros de cualquier tamaño; `float()` no


@pytest.mark.parametrize("value", [GIGANTE, -GIGANTE, 2 ** 1024])
def test_un_entero_gigante_no_revienta_ni_la_escritura_ni_la_lectura(value):
    """`math.isfinite(int enorme)` lanza OverflowError: en escritura era un 500
    en vez de 422 y en lectura rompía la promesa de que un dato corrupto nunca
    deja un canvas sin abrir."""
    raw = {"ok": [{"x": 1, "y": 2}], "gigante": [{"x": value, "y": 1}], "otro": [{"x": 1, "y": value}]}
    err = routes_error({"gigante": raw["gigante"]})
    assert err is not None and "out of range" in err
    assert clean_routes(raw) == {"ok": [{"x": 1, "y": 2}]}
    doc = SubjectAreaDoc.model_validate(_sa(routes=raw))
    assert doc.model_dump()["routes"] == {"ok": [{"x": 1, "y": 2}]}


@pytest.mark.parametrize("wire_id", ["a\x00b", "a\nb", "\t", "x" * (MAX_ROUTE_ID_LEN + 1)])
def test_id_de_wire_invalido(wire_id):
    """Caracteres de control (un `\\u0000` no es representable en JSONB) e ids
    desmedidos: 422 al escribir, se ignoran al leer."""
    err = routes_error({wire_id: [{"x": 1, "y": 2}]})
    assert err is not None and "wire id" in err
    assert clean_routes({wire_id: [{"x": 1, "y": 2}], "ok": [{"x": 3, "y": 4}]}) == {"ok": [{"x": 3, "y": 4}]}


def test_id_de_wire_en_el_tope_de_largo_es_valido():
    wire_id = "x" * MAX_ROUTE_ID_LEN
    assert routes_error({wire_id: [{"x": 1, "y": 2}]}) is None
    assert clean_routes({wire_id: [{"x": 1, "y": 2}]}) == {wire_id: [{"x": 1, "y": 2}]}


@pytest.mark.parametrize("wire_id", ["a.b", "$x", "subsym-sym-1", "con espacio", "ñandú-é", "r/1"])
def test_ids_de_wire_con_caracteres_normales_pasan(wire_id):
    assert routes_error({wire_id: [{"x": 1, "y": 2}]}) is None
    assert clean_routes({wire_id: [{"x": 1, "y": 2}]}) == {wire_id: [{"x": 1, "y": 2}]}


def test_tope_de_wires_trazados_por_canvas():
    """Sin tope, un cliente podía grabar 20 000 wires × 32 puntos (12 MB) en un
    canvas. La lectura sigue tolerante: no falla, sea cual sea el tamaño."""
    point = [{"x": 1, "y": 2}]
    exact = {f"r{i}": point for i in range(MAX_ROUTES)}
    assert routes_error(exact) is None
    over = {**exact, "uno-mas": point}
    err = routes_error(over)
    assert err is not None and "at most" in err and str(MAX_ROUTES) in err
    assert len(clean_routes(over)) == MAX_ROUTES + 1
