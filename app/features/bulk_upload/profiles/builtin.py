"""Perfil built-in de `Plantilla.xlsx` (doc 78 §9). Los UDP se declaran por
(level, view, name) y `materialize` los resuelve a ids contra el catálogo del
proyecto — lo usan el seed del one-shot y «Create default profile». PURO."""
from __future__ import annotations

import copy

from app.core.facets import normalize_udp_view

from ..normalize import norm_key

BUILTIN_ORIGIN = "builtin:plantilla-bcp"


def _f(header: str, field: str, *rules: dict, default: str | None = None) -> dict:
    return {"header": header, "target": {"kind": "field", "field": field}, "rules": list(rules), "defaultValue": default}


def _u(header: str, *refs: tuple[str, str, str]) -> dict:
    return {"header": header,
            "target": {"kind": "udp", "udpRefs": [{"level": lv, "view": vw, "name": nm} for lv, vw, nm in refs]},
            "rules": []}


def _i(header: str) -> dict:
    return {"header": header, "target": {"kind": "ignore"}, "rules": []}


_REQ = {"type": "required", "severity": "error"}


def _max(n: int) -> dict:
    return {"type": "maxLength", "value": n, "severity": "error"}


PLANTILLA_BCP: dict = {
    "name": "Plantilla BCP",
    "description": ("Sheets Cargar_Tablas / Cargar_Campos of Plantilla.xlsx (header row 5). Table UDPs feed both "
                    "facets; the column classification feeds Attribute (logical) and Column (physical)."),
    "isDefault": True,
    "origin": BUILTIN_ORIGIN,
    "sheets": {
        "tables": {"name": "Cargar_Tablas", "required": True, "headerRow": 5, "mappings": [
            _f("SUBJECT", "subject"),
            _f("DIAGRAMA", "diagram"),
            _f("ESQUEMA", "schema"),
            _f("TABLA_LOGICO", "logicalName", _REQ, _max(80)),
            _f("TABLA_FISICA", "physicalName", _max(80)),
            _f("DEF_TABLA", "description"),
            _u("UDP_Tabla_Cross", ("table", "physical", "Tabla Cross")),
            _u("UDP_Tipo_de_Entidad", ("table", "physical", "Tipo de Entidad"), ("table", "logical", "Tipo de Entidad")),
            _u("Clasificacion_del_Dato", ("table", "physical", "Clasificacion del Dato"),
               ("table", "logical", "Clasificacion del Dato")),
            _u("UDP_Universal", ("table", "physical", "Universal"), ("table", "logical", "Universal")),
            _u("UDP_Dominio_Principal", ("table", "physical", "Dominio Principal"),
               ("table", "logical", "Dominio Principal")),
            _u("UDP_Tipo_de_Carga", ("table", "physical", "Tipo de Carga")),
        ]},
        "columns": {"name": "Cargar_Campos", "required": True, "headerRow": 5, "mappings": [
            _f("TABLA_LOGICO", "tableRef", _REQ, _max(80)),
            _f("CAMPO_LOGICO", "logicalName", _REQ, _max(120)),
            _f("CAMPO_FISICO", "physicalName", _max(80)),
            _f("DEF_ATRIBUTO", "description"),
            _f("PARENT_DOMAIN", "parentDomain"),
            _f("TIPO_DATO", "dataType"),
            _u("UDP Clasificacion del Dato", ("column", "physical", "Clasificacion del Dato"),
               ("column", "logical", "Clasificacion del Dato")),
            _u("UDP Campo Cross", ("column", "physical", "Campo Cross"), ("column", "logical", "Atributo Cross")),
            _u("UDP Particion", ("column", "physical", "Particion")),
            _f("PK", "pk"),
            _i("LOGICO"),
            _i("FISICO"),
        ]},
    },
    "policies": {"onExistingTable": "update", "onExistingColumn": "update", "unknownHeaders": "warn"},
}


def materialize(spec: dict, udp_defs: list[dict]) -> tuple[dict, list[str]]:
    """Resuelve `udpRefs` → `udpIds` contra las defs vivas del proyecto. Def
    ausente ⇒ aviso; mapeo sin ninguna def ⇒ `ignore` (+ aviso)."""
    by_key: dict[tuple[str, str, str], dict] = {}
    for d in udp_defs:
        level = d.get("level") or "column"
        by_key.setdefault((level, normalize_udp_view(level, d.get("view")), norm_key(d.get("name"))), d)
    body = copy.deepcopy(spec)
    warnings: list[str] = []
    for sheet in body["sheets"].values():
        for m in sheet["mappings"]:
            target = m["target"]
            if target.get("kind") != "udp":
                continue
            ids: list[str] = []
            for ref in target.pop("udpRefs", []):
                d = by_key.get((ref["level"], ref["view"], norm_key(ref["name"])))
                if d is None:
                    warnings.append(f"{sheet['name']} · '{m['header']}': UDP '{ref['name']}' ({ref['level']}, "
                                    f"{ref['view']}) doesn't exist in this project — skipped.")
                    continue
                ids.append(str(d["id"]))
            if ids:
                m["target"] = {"kind": "udp", "udpIds": ids}
            else:
                m["target"] = {"kind": "ignore"}
                warnings.append(f"{sheet['name']} · '{m['header']}': no UDP could be resolved — "
                                "column left unmapped (ignore).")
    return body, warnings
