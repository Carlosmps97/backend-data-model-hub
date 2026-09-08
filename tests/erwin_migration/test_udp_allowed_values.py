"""Doc 69: las defs Logical/Physical homónimas ya NO se colapsan — una def de
plataforma por (level, view, nombre); la lista de valores explícita se conserva."""
from scripts.erwin_migration.erwin_parser import ErwinUdpDef
from scripts.erwin_migration.policies import PARTITION_UDP_KEY, udp_datatype, udp_defs_by_view, udp_key


def _def(name, mode, code, default="", allowed=(), owner="Entity"):
    return ErwinUdpDef(id=f"{name}|{mode}", full_name=f"{owner}.{mode}.{name}",
                       owner_class=owner, view_mode=mode, short_name=name,
                       data_type_code=code, default=default, allowed_values=list(allowed))


def test_logical_y_physical_son_defs_distintas_con_su_propia_lista():
    defs = {"a": _def("Clasificacion del Dato", "Logical", "6", allowed=["A", "B"]),
            "b": _def("Clasificacion del Dato", "Physical", "6", allowed=["B", "C"])}
    out = udp_defs_by_view(defs)
    assert set(out) == {"table|logical|Clasificacion del Dato", "table|physical|Clasificacion del Dato"}
    assert out["table|logical|Clasificacion del Dato"]["allowed_values"] == ["A", "B"]
    assert out["table|physical|Clasificacion del Dato"]["allowed_values"] == ["B", "C"]
    assert out["table|logical|Clasificacion del Dato"]["view"] == "logical"


def test_view_y_model_siempre_physical():
    out = udp_defs_by_view({"v": _def("Tipo de Vista", "Logical", "6", allowed=["V1"], owner="View"),
                            "m": _def("Database", "Physical", "2", owner="Model")})
    assert set(out) == {"view|physical|Tipo de Vista", "canvas|physical|Database"}


def test_text_def_sin_allowed_values_y_udp_datatype():
    entry = udp_defs_by_view({"t": _def("Filtro", "Physical", "2")})["table|physical|Filtro"]
    assert entry["dataType"] == "string" and entry["allowed_values"] == []
    assert udp_datatype("6") == "list" and udp_datatype("2") == "string"


def test_partition_key_es_la_def_fisica_de_columna():
    assert PARTITION_UDP_KEY == udp_key("column", "physical", "Particion")
