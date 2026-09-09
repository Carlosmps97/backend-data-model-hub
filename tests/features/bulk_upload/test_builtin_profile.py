"""Doc 78 §9: el perfil de Plantilla.xlsx se materializa contra el catálogo fijo
de UDPs (doc 68/69) sin pérdida; una def ausente deja la columna en `ignore`."""
from __future__ import annotations

from app.features.bulk_upload.profiles.builtin import BUILTIN_ORIGIN, PLANTILLA_BCP, materialize
from app.features.bulk_upload.profiles.models import validate_profile
from scripts.erwin_migration.standard_udps import FIXED_UDPS

DEFS = [{**d, "id": f"u{i}"} for i, d in enumerate(FIXED_UDPS)]


def _by_header(body, role):
    return {m["header"]: m for m in body["sheets"][role]["mappings"]}


def test_spec_declara_hojas_y_fila_de_cabecera():
    assert (PLANTILLA_BCP["sheets"]["tables"]["name"], PLANTILLA_BCP["sheets"]["columns"]["name"]) == ("Cargar_Tablas", "Cargar_Campos")
    assert PLANTILLA_BCP["sheets"]["tables"]["headerRow"] == 5 and PLANTILLA_BCP["sheets"]["columns"]["headerRow"] == 5
    assert BUILTIN_ORIGIN == "builtin:plantilla-bcp"


def test_materializa_todos_los_udp_contra_el_catalogo_fijo():
    body, warnings = materialize(PLANTILLA_BCP, DEFS)
    assert warnings == []
    assert validate_profile(body, DEFS) == []
    assert "udpRefs" not in str(body)
    t, c = _by_header(body, "tables"), _by_header(body, "columns")
    names = {d["id"]: (d["name"], d["view"]) for d in DEFS}
    assert sorted(names[u] for u in t["UDP_Tipo_de_Entidad"]["target"]["udpIds"]) == [("Tipo de Entidad", "logical"), ("Tipo de Entidad", "physical")]
    assert sorted(names[u] for u in t["Clasificacion_del_Dato"]["target"]["udpIds"]) == [("Clasificacion del Dato", "logical"), ("Clasificacion del Dato", "physical")]
    assert sorted(names[u] for u in t["UDP_Universal"]["target"]["udpIds"]) == [("Universal", "logical"), ("Universal", "physical")]
    assert sorted(names[u] for u in t["UDP_Dominio_Principal"]["target"]["udpIds"]) == [("Dominio Principal", "logical"), ("Dominio Principal", "physical")]
    assert [names[u] for u in t["UDP_Tipo_de_Carga"]["target"]["udpIds"]] == [("Tipo de Carga", "physical")]
    assert [names[u] for u in t["UDP_Tabla_Cross"]["target"]["udpIds"]] == [("Tabla Cross", "physical")]
    assert sorted(names[u] for u in c["UDP Campo Cross"]["target"]["udpIds"]) == [("Atributo Cross", "logical"), ("Campo Cross", "physical")]
    assert sorted(names[u] for u in c["UDP Clasificacion del Dato"]["target"]["udpIds"]) == [("Clasificacion del Dato", "logical"), ("Clasificacion del Dato", "physical")]
    assert [names[u] for u in c["UDP Particion"]["target"]["udpIds"]] == [("Particion", "physical")]
    assert c["LOGICO"]["target"] == {"kind": "ignore"} and c["FISICO"]["target"] == {"kind": "ignore"}
    assert c["PK"]["target"] == {"kind": "field", "field": "pk"}
    assert [r["type"] for r in t["TABLA_LOGICO"]["rules"]] == ["required", "maxLength"]
    assert [(r["type"], r.get("value")) for r in c["CAMPO_LOGICO"]["rules"]] == [("required", None), ("maxLength", 120)]
    assert body["origin"] == BUILTIN_ORIGIN and body["isDefault"] is True and body["name"] == "Plantilla BCP"
    assert PLANTILLA_BCP["sheets"]["columns"]["mappings"][8]["target"]["udpRefs"]   # el spec no se muta


def test_def_ausente_deja_ignore_con_aviso():
    sin_particion = [d for d in DEFS if d["name"] != "Particion"]
    body, warnings = materialize(PLANTILLA_BCP, sin_particion)
    assert _by_header(body, "columns")["UDP Particion"]["target"] == {"kind": "ignore"}
    assert any("Particion" in w and "UDP Particion" in w for w in warnings)
    assert validate_profile(body, sin_particion) == []


def test_def_parcial_conserva_la_que_existe():
    sin_logica = [d for d in DEFS if not (d["name"] == "Tipo de Entidad" and d["view"] == "logical")]
    body, warnings = materialize(PLANTILLA_BCP, sin_logica)
    ids = _by_header(body, "tables")["UDP_Tipo_de_Entidad"]["target"]["udpIds"]
    assert len(ids) == 1 and len(warnings) == 1 and "logical" in warnings[0]
