"""Forma del perfil de carga (doc 78 §4) y su validación estructural. PURO.

`validate_profile` trabaja sobre dicts (model_dump) para que lo compartan el
service (422), el endpoint /validate, el seed y el arranque de cada job (contra
las defs UDP vivas del proyecto)."""
from __future__ import annotations

import re
import uuid

from pydantic import BaseModel, Field

from app.core.models import DOC_CONFIG

from ..normalize import clean_text, norm_name
from ..parser import header_key
from ..standards import profile_default_error, udp_value

SHEET_ROLES = ("tables", "columns")
LEVEL_OF_ROLE = {"tables": "table", "columns": "column"}
IDENTITY_FIELDS = {"tables": ("logicalName", "physicalName"), "columns": ("tableRef", "logicalName", "physicalName")}
SEVERITIES = ("error", "warning")
MAX_PATTERN_LEN = 200

# field · label · attr (atributo de TableRow/ColumnRow) · required · key · mustExist
FIELD_TARGETS: dict[str, list[dict]] = {
    "tables": [
        {"field": "logicalName", "label": "Logical name", "attr": "logical", "required": True, "key": True, "mustExist": False},
        {"field": "physicalName", "label": "Physical name", "attr": "physical", "required": False, "key": False, "mustExist": False},
        {"field": "schema", "label": "Schema", "attr": "schema", "required": False, "key": False, "mustExist": True},
        {"field": "description", "label": "Definition", "attr": "description", "required": False, "key": False, "mustExist": False},
        {"field": "subject", "label": "Subject (folder)", "attr": "subject", "required": False, "key": False, "mustExist": True},
        {"field": "space", "label": "Space (root folder)", "attr": "space", "required": False, "key": False, "mustExist": True},
        {"field": "diagram", "label": "Diagram (canvas)", "attr": "diagram", "required": False, "key": False, "mustExist": True},
        {"field": "project", "label": "Project (check)", "attr": "project", "required": False, "key": False, "mustExist": False},
    ],
    "columns": [
        {"field": "tableRef", "label": "Table (logical name)", "attr": "table_logical", "required": True, "key": True, "mustExist": False},
        {"field": "logicalName", "label": "Logical name", "attr": "logical", "required": True, "key": False, "mustExist": False},
        {"field": "physicalName", "label": "Physical name", "attr": "physical", "required": False, "key": False, "mustExist": False},
        {"field": "description", "label": "Definition", "attr": "description", "required": False, "key": False, "mustExist": False},
        {"field": "parentDomain", "label": "Parent domain", "attr": "domain", "required": False, "key": False, "mustExist": True},
        {"field": "dataType", "label": "Data type", "attr": "data_type", "required": False, "key": False, "mustExist": False},
        {"field": "pk", "label": "Primary key mark", "attr": "pk", "required": False, "key": False, "mustExist": False},
    ],
}
FIELDS_BY_ROLE: dict[str, dict[str, dict]] = {role: {f["field"]: f for f in specs} for role, specs in FIELD_TARGETS.items()}

RULE_TYPES: list[dict] = [
    {"type": "required", "label": "Required", "valueKind": None, "appliesTo": "any"},
    {"type": "maxLength", "label": "Max length", "valueKind": "int", "appliesTo": "any"},
    {"type": "pattern", "label": "Pattern (regex)", "valueKind": "regex", "appliesTo": "any"},
    {"type": "allowedValues", "label": "Allowed values", "valueKind": "list", "appliesTo": "any"},
    {"type": "uniqueInFile", "label": "Unique in file", "valueKind": None, "appliesTo": "any"},
    {"type": "mustExist", "label": "Must exist (don't create)", "valueKind": None, "appliesTo": "mustExist"},
]
RULE_TYPE_SET = frozenset(r["type"] for r in RULE_TYPES)
POLICY_VALUES: dict[str, tuple[str, ...]] = {
    "onExistingTable": ("update", "reject"),
    "onExistingColumn": ("update", "reject"),
    "unknownHeaders": ("warn", "ignore", "reject"),
}


def key_field(role: str) -> str:
    """Campo CLAVE de la hoja: ancla de la fila de cabecera y obligatorio."""
    return next(f["field"] for f in FIELD_TARGETS[role] if f["key"])


def catalog() -> dict:
    """Catálogo fijo para el editor (campos por hoja, tipos de regla, políticas)."""
    return {"fields": {role: [dict(f) for f in specs] for role, specs in FIELD_TARGETS.items()},
            "ruleTypes": [dict(r) for r in RULE_TYPES],
            "policies": {k: list(v) for k, v in POLICY_VALUES.items()}}


# ── Pydantic ───────────────────────────────────────────────────────────────
class RuleSpec(BaseModel):
    type: str
    value: int | str | list[str] | None = None
    severity: str = "error"


class TargetSpec(BaseModel):
    kind: str = "ignore"               # field | udp | ignore
    field: str | None = None
    udpIds: list[str] = []


class MappingSpec(BaseModel):
    header: str
    target: TargetSpec = Field(default_factory=TargetSpec)
    rules: list[RuleSpec] = []
    defaultValue: str | None = None


class SheetSpec(BaseModel):
    name: str
    required: bool = True
    headerRow: int | None = None
    mappings: list[MappingSpec] = []


class PoliciesSpec(BaseModel):
    onExistingTable: str = "update"
    onExistingColumn: str = "update"
    unknownHeaders: str = "warn"


class UploadProfileBody(BaseModel):
    """Lo que edita la web / siembra el kit (sin id, proyecto ni auditoría)."""
    name: str
    description: str | None = None
    isDefault: bool = False
    sheets: dict[str, SheetSpec] = {}
    policies: PoliciesSpec = Field(default_factory=PoliciesSpec)


class UploadProfileDoc(BaseModel):
    model_config = DOC_CONFIG

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    # Doc 75 D1: alcance por proyecto — obligatorio, lo estampa el servidor.
    projectId: str
    name: str
    description: str | None = None
    isDefault: bool = False
    origin: str = "user"                # 'user' | 'builtin:<slug>'
    sheets: dict[str, SheetSpec] = {}
    policies: PoliciesSpec = Field(default_factory=PoliciesSpec)
    createdBy: str | None = None
    updatedBy: str | None = None


# ── Validación ─────────────────────────────────────────────────────────────
def validate_profile(profile: dict, udp_defs: list[dict]) -> list[dict]:
    """Problemas estructurales `[{path, code, message}]`; vacío = perfil válido."""
    problems: list[dict] = []

    def add(path: str, code: str, message: str) -> None:
        problems.append({"path": path, "code": code, "message": message})

    if not clean_text(profile.get("name")):
        add("name", "name-empty", "The profile needs a name.")
    defs_by_id = {str(d.get("id")): d for d in udp_defs if d.get("id")}
    sheets = profile.get("sheets") or {}
    names_seen: dict[str, str] = {}
    for role in SHEET_ROLES:
        spec = sheets.get(role)
        if not isinstance(spec, dict):
            add(f"sheets.{role}", "sheet-missing", f"The profile needs a '{role}' sheet block.")
            continue
        sname = clean_text(spec.get("name"))
        if not sname:
            add(f"sheets.{role}.name", "sheet-name-empty", "Give the sheet a name (as it appears in Excel).")
        else:
            key = norm_name(sname)
            if key in names_seen:
                add(f"sheets.{role}.name", "sheet-names-equal", "Both sheets have the same name.")
            names_seen[key] = role
        hr = spec.get("headerRow")
        if hr is not None and (isinstance(hr, bool) or not isinstance(hr, int) or hr < 1):
            add(f"sheets.{role}.headerRow", "header-row-invalid",
                "Header row must be a positive row number (or empty to auto-detect).")
        _validate_mappings(role, spec.get("mappings") or [], defs_by_id, add)
    pol = profile.get("policies") or {}
    for k, allowed in POLICY_VALUES.items():
        if pol.get(k, allowed[0]) not in allowed:
            add(f"policies.{k}", "policy-invalid", f"'{k}' must be one of: {', '.join(allowed)}.")
    return problems


def _validate_mappings(role: str, mappings: list[dict], defs_by_id: dict[str, dict], add) -> None:
    level = LEVEL_OF_ROLE[role]
    fields_seen: dict[str, int] = {}
    headers_seen: dict[str, int] = {}
    udp_seen: dict[str, int] = {}
    for i, m in enumerate(mappings):
        path = f"sheets.{role}.mappings[{i}]"
        header = clean_text(m.get("header"))
        if not header:
            add(f"{path}.header", "header-empty", "Every mapping needs the Excel header text.")
        else:
            hk = header_key(header)
            if hk in headers_seen:
                add(f"{path}.header", "header-duplicate",
                    f"'{header}' is mapped twice (see mapping #{headers_seen[hk] + 1}).")
            headers_seen.setdefault(hk, i)
        target = m.get("target") or {}
        kind = target.get("kind")
        field_spec: dict | None = None
        if kind == "field":
            field = target.get("field")
            field_spec = FIELDS_BY_ROLE[role].get(field or "")
            if field_spec is None:
                add(f"{path}.target", "field-unknown", f"'{field}' is not a field of the {role} sheet.")
            else:
                if field in fields_seen:
                    add(f"{path}.target", "field-duplicate",
                        f"'{field_spec['label']}' is already mapped (mapping #{fields_seen[field] + 1}).")
                fields_seen.setdefault(field, i)
        elif kind == "udp":
            ids = [str(u) for u in (target.get("udpIds") or [])]
            if not ids:
                add(f"{path}.target", "udp-empty", "Pick at least one UDP for this column.")
            for uid in ids:
                d = defs_by_id.get(uid)
                if d is None:
                    add(f"{path}.target", "udp-unknown", f"UDP '{uid}' doesn't exist in this project's Data Standards.")
                elif (d.get("level") or "column") != level:
                    add(f"{path}.target", "udp-wrong-level",
                        f"UDP '{d.get('name')}' is a {d.get('level')}-level UDP; the {role} sheet needs {level}-level UDPs.")
                if uid in udp_seen:
                    add(f"{path}.target", "udp-duplicate",
                        f"UDP '{(d or {}).get('name', uid)}' is already mapped (mapping #{udp_seen[uid] + 1}).")
                udp_seen.setdefault(uid, i)
                # Doc 105 (ronda 6): el default se valida por el TIPO del UDP al
                # guardar el perfil — antes fallaba recién en cada fila nueva.
                default = clean_text(m.get("defaultValue"))
                if d is not None and default:
                    _value, err = udp_value(d, default)
                    if err:
                        add(f"{path}.defaultValue", "default-invalid", profile_default_error(d, default, err))
        elif kind != "ignore":
            add(f"{path}.target", "target-invalid", "Target must be a field, one or more UDPs, or 'ignore'.")
        if clean_text(m.get("defaultValue")) and kind == "field" and target.get("field") in IDENTITY_FIELDS[role]:
            add(f"{path}.defaultValue", "default-not-applicable",
                "Identity columns (names, table reference) can't have a default value.")
        _validate_rules(path, m.get("rules") or [], kind, field_spec, add)
    for field, fs in FIELDS_BY_ROLE[role].items():
        if fs["required"] and field not in fields_seen:
            add(f"sheets.{role}.mappings", "field-required-missing", f"Map a column of the {role} sheet to '{fs['label']}'.")


def _validate_rules(path: str, rules: list[dict], kind: str | None, field_spec: dict | None, add) -> None:
    seen: set[str] = set()
    for j, r in enumerate(rules):
        rpath = f"{path}.rules[{j}]"
        rtype = r.get("type")
        if rtype not in RULE_TYPE_SET:
            add(rpath, "rule-unknown", f"Unknown rule '{rtype}'.")
            continue
        if rtype in seen:
            add(rpath, "rule-duplicate", f"Rule '{rtype}' is repeated.")
        seen.add(rtype)
        if r.get("severity", "error") not in SEVERITIES:
            add(f"{rpath}.severity", "rule-value-invalid", "Severity must be 'error' or 'warning'.")
        value = r.get("value")
        if rtype == "maxLength" and (isinstance(value, bool) or not isinstance(value, int) or value < 1):
            add(f"{rpath}.value", "rule-value-invalid", "Max length must be a whole number of at least 1.")
        elif rtype == "pattern":
            ok = isinstance(value, str) and 0 < len(value) <= MAX_PATTERN_LEN
            if ok:
                try:
                    re.compile(value)
                except re.error:
                    ok = False
            if not ok:
                add(f"{rpath}.value", "rule-value-invalid",
                    f"Pattern must be a valid regular expression (max {MAX_PATTERN_LEN} characters).")
        elif rtype == "allowedValues":
            if not (isinstance(value, list) and value and all(isinstance(v, str) and clean_text(v) for v in value)):
                add(f"{rpath}.value", "rule-value-invalid", "Allowed values must be a non-empty list of texts.")
        elif rtype == "mustExist" and not (kind == "field" and field_spec and field_spec["mustExist"]):
            add(rpath, "rule-not-applicable",
                "'Must exist' only applies to schema, subject, space, diagram and parent domain.")
