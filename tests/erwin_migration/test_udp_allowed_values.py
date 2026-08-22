"""UDP: la lista EXPLÍCITA de valores permitidos (`tag_Udp_Values_List`) entra al
colapsado — estándar COMPLETO aunque no se use en ninguna tabla (owner 2026-07-18)."""
from scripts.erwin_migration.erwin_parser import ErwinUdpDef
from scripts.erwin_migration.policies import collapse_udp_defs, udp_datatype


def _def(name, mode, code, default="", allowed=()):
    return ErwinUdpDef(id=f"{name}|{mode}", full_name=f"Entity.{mode}.{name}",
                       owner_class="Entity", view_mode=mode, short_name=name,
                       data_type_code=code, default=default, allowed_values=list(allowed))


def test_collapse_unions_allowed_values_preserving_order():
    defs = {
        "a": _def("Clasificacion del Dato", "Logical", "6", allowed=["A", "B"]),
        "b": _def("Clasificacion del Dato", "Physical", "6", allowed=["B", "C"]),
    }
    entry = collapse_udp_defs(defs)["table|Clasificacion del Dato"]
    assert entry["dataType"] == "list"
    assert entry["allowed_values"] == ["A", "B", "C"]  # unión, orden, sin dup


def test_list_def_with_zero_usage_keeps_its_enum():
    entry = collapse_udp_defs(
        {"x": _def("Tipo de Vista", "Physical", "6", allowed=["V1", "V2"])})["table|Tipo de Vista"]
    assert entry["allowed_values"] == ["V1", "V2"]


def test_text_def_has_no_allowed_values():
    entry = collapse_udp_defs({"t": _def("Filtro", "Physical", "2")})["table|Filtro"]
    assert entry["dataType"] != "list"
    assert entry["allowed_values"] == []


def test_udp_datatype_code():
    assert udp_datatype("6") == "list"
    assert udp_datatype("2") == "string"
