"""Diff de campos ANTES→DESPUÉS por entidad (doc 31 — popup "Change details"
de la revisión). PURO — sin BD: el service arma los insumos (`before` =
imagen histórica si el cambio ya la trae estampada del publish, o el documento
PUBLICADO vivo durante la revisión; `after` = payload del cambio, que por regla
de la casa es SIEMPRE el doc completo) y acá solo se compara y se vuelve legible.

Filosofía: honesto pero legible. Se comparan los documentos completos
excluyendo SOLO ruido interno (timestamps, flgactive, `layout`/`drawings` del
canvas — posiciones ensuciarían todo diff de modelo). Las referencias se
resuelven a NOMBRE (UDP por defId, Parent Domain, tablas/columnas de relaciones
y vistas) y un campo sin label conocido sale con la key prettificada — un campo
nuevo del modelo nunca queda invisible para el revisor.
"""
from __future__ import annotations

import json
import re

# Ruido global: identidad/timestamps/soft-delete — jamás son "el cambio".
NOISE = {"id", "_id", "csId", "at", "createdAt", "updatedAt", "flgactive"}
NOISE_BY_COLLECTION: dict[str, set[str]] = {
    # layout/drawings = posiciones y shapes del canvas: mover cajitas no es un
    # cambio de MODELO revisable campo a campo.
    "subject_areas": {"layout", "drawings"},
    # tableId: el árbol ya da el contexto; typeOverridden: flag interno del naming.
    "canonical_columns": {"tableId", "typeOverridden"},
    # tableId/sourceTableIds: compat — las filas por fuente (sources) los cubren;
    # sql: derivado del editor de vistas (sources es el canónico).
    # customColumns: derivadas del customSql (el campo revisable es el script).
    "views": {"tableId", "sourceTableIds", "sql", "customColumns"},
    # subtypeSymbolId: UUID interno del grupo de subcategoría (doc 53) — el
    # revisor ya ve "Subcategory" + padre/hijo; el id del símbolo es ruido.
    "relationships": {"subtypeSymbolId"},
}

# key → label (en INGLÉS, regla UI); el ORDEN es el de presentación.
FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "canonical_tables": (
        ("physicalName", "Physical name"), ("logicalName", "Logical name"),
        ("schema", "Schema"), ("description", "Definition"),
    ),
    "canonical_columns": (
        ("physicalName", "Physical name"), ("logicalName", "Logical name"),
        ("dataType", "Data type"), ("isNullable", "Nullable"),
        ("isPrimaryKey", "Primary key"), ("pkPosition", "Key position"),
        ("isForeignKey", "Foreign key"), ("isPartition", "Partition"),
        ("ordinal", "Order"), ("parentDomainId", "Parent domain"),
        ("description", "Definition"),
    ),
    "views": (
        ("name", "Name"), ("schema", "Schema"), ("description", "Definition"),
        ("showOnCanvas", "On canvas"), ("filter", "Filter"),
        ("joinOverride", "Join condition"), ("tags", "Tags"),
        ("customSql", "Custom SQL"),
    ),
    "relationships": (
        ("parentTableId", "Parent table"), ("childTableId", "Child table"),
        ("parentCardinality", "Parent cardinality"),
        ("childCardinality", "Child cardinality"), ("identifying", "Identifying"),
        ("subcategory", "Subcategory"),
    ),
    "schemas": (("name", "Name"), ("description", "Definition")),
    "projects": (("name", "Name"), ("description", "Definition")),
    "folders": (("name", "Name"), ("projectId", "Project")),
    "subject_areas": (("name", "Name"), ("projectId", "Project"),
                      ("folderId", "Folder")),
}

# Claves con render propio (no pasan por el genérico).
_SPECIAL = {"udpValues", "sources", "pairs", "tableIds"}


# ── Helpers de valor ───────────────────────────────────────────────────────


def _clean(v):
    """'' ≡ None (la UI guarda vacío como ausencia)."""
    return None if isinstance(v, str) and v == "" else v


def _skippable(v) -> bool:
    """Valor sin señal para un created/deleted: None, '', False, [] o {}.
    OJO: 0 NO es skippable (ordinal 0 / pkPosition 0 son valores reales)."""
    if v is None or v is False:
        return True
    if isinstance(v, str) and v == "":
        return True
    if isinstance(v, (list, dict)) and not v:
        return True
    return False


def _eq(x, y) -> bool:
    if isinstance(x, (dict, list)) or isinstance(y, (dict, list)):
        return json.dumps(x, sort_keys=True, default=str) == \
               json.dumps(y, sort_keys=True, default=str)
    return x == y


def _short_json(v, limit: int = 160) -> str:
    s = json.dumps(v, ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _prettify(key: str) -> str:
    """camelCase/snake_case → 'Camel case' (fallback para keys sin label)."""
    words = re.sub(r"(?<!^)(?=[A-Z])", " ", key.replace("_", " ")).split()
    return " ".join(w.lower() for w in words).capitalize() if words else key


def _n(count: int, noun: str) -> str:
    return f"{count} {noun}{'' if count == 1 else 's'}"


def _norm(doc: dict | None) -> dict | None:
    """Alias `sql_schema` → `schema` (forma persistida vs modelo pydantic)."""
    if not doc:
        return None
    d = dict(doc)
    if d.get("schema") is None and d.get("sql_schema") is not None:
        d["schema"] = d["sql_schema"]
    d.pop("sql_schema", None)
    return d


def _resolve(key: str, v, res: dict):
    """Valor CRUDO → valor de DISPLAY (referencias a nombre, contenedores
    compactos). La comparación de igualdad SIEMPRE es sobre el crudo."""
    if v is None:
        return None
    if key == "parentDomainId":
        return res.get("domains", {}).get(v, v)
    if key in ("parentTableId", "childTableId", "sourceTableId", "targetTableId"):
        return res.get("tables", {}).get(v, v)
    if key == "projectId":
        return res.get("projects", {}).get(v, v)
    if key == "folderId":
        return res.get("folders", {}).get(v, v)
    if isinstance(v, list) and all(not isinstance(x, (dict, list)) for x in v):
        return ", ".join(str(x) for x in v)
    if isinstance(v, (dict, list)):
        return _short_json(v)
    return v


# ── Filas especiales ───────────────────────────────────────────────────────


def _udp_rows(b: dict, a: dict, action: str, res: dict) -> list[dict]:
    """Una fila por UDP con su NOMBRE (defId → name del catálogo)."""
    bu = {k: _clean(v) for k, v in (b.get("udpValues") or {}).items()}
    au = {k: _clean(v) for k, v in (a.get("udpValues") or {}).items()}
    names = res.get("udp", {})
    rows: list[dict] = []
    for def_id in sorted(set(bu) | set(au), key=lambda d: str(names.get(d, d)).lower()):
        bv, av = bu.get(def_id), au.get(def_id)
        if action == "modified" and _eq(bv, av):
            continue
        if action == "created":
            if _skippable(av):
                continue
            bv = None
        if action == "deleted":
            if _skippable(bv):
                continue
            av = None
        rows.append({"key": f"udp:{def_id}", "label": f"UDP · {names.get(def_id, def_id)}",
                     "before": bv, "after": av})
    return rows


def _pairs_disp(pairs: list | None, res: dict) -> str | None:
    cn = res.get("columns", {})
    parts = []
    for p in pairs or []:
        pc, cc = p.get("parentColumnId"), p.get("childColumnId")
        s = f"{cn.get(pc, pc or '?')} → {cn.get(cc, cc or '?')}"
        if p.get("roleName"):
            s += f" (as {p['roleName']})"
        parts.append(s)
    return " · ".join(parts) or None


def _pair_rows(b: dict, a: dict, action: str, res: dict) -> list[dict]:
    bp, ap = b.get("pairs") or [], a.get("pairs") or []
    if action == "modified" and _eq(bp, ap):
        return []
    before = _pairs_disp(bp, res) if action != "created" else None
    after = _pairs_disp(ap, res) if action != "deleted" else None
    if before is None and after is None:
        return []
    return [{"key": "pairs", "label": "Pairs", "before": before, "after": after}]


def _sources_by_table(doc: dict) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for s in doc.get("sources") or []:
        out.setdefault(s.get("tableId") or "__", []).append(s)
    return out


def _src_col_name(entry: dict) -> str:
    return (entry.get("outputAlias") or entry.get("column")
            or _short_json(entry.get("expression") or "?", 24))


def _src_signature(entry: dict) -> tuple:
    """Lo que hace 'distinta' a una columna de vista sin cambiar de nombre:
    expresión, cast y definición propia."""
    return (_clean(entry.get("column")), _clean(entry.get("outputAlias")),
            _clean(entry.get("expression")), _clean(entry.get("castType")),
            _clean(entry.get("description")))


def _colsum(names: list[str]) -> str:
    head = ", ".join(names[:8]) + ("…" if len(names) > 8 else "")
    return f"{_n(len(names), 'column')}: {head}" if names else "0 columns"


def _view_source_rows(b: dict, a: dict, action: str, res: dict) -> list[dict]:
    """Por FUENTE de la vista: columnas ± y definiciones de columna cambiadas."""
    bs, as_ = _sources_by_table(b), _sources_by_table(a)
    tnames = res.get("tables", {})
    rows: list[dict] = []
    for tid in list(as_) + [t for t in bs if t not in as_]:
        tname = tnames.get(tid, tid if tid != "__" else "view")
        b_entries = {_src_col_name(e): e for e in bs.get(tid, [])}
        a_entries = {_src_col_name(e): e for e in as_.get(tid, [])}
        if action == "created" or not b_entries:
            if a_entries:
                rows.append({"key": f"src:{tid}", "label": f"Columns · {tname}",
                             "before": None, "after": _colsum(list(a_entries))})
            continue
        if action == "deleted" or not a_entries:
            if b_entries:
                rows.append({"key": f"src:{tid}", "label": f"Columns · {tname}",
                             "before": _colsum(list(b_entries)), "after": None})
            continue
        added = [n for n in a_entries if n not in b_entries]
        removed = [n for n in b_entries if n not in a_entries]
        if added or removed:
            parts = [", ".join(f"+{n}" for n in added)] if added else []
            if removed:
                parts.append(", ".join(f"−{n}" for n in removed))
            rows.append({"key": f"src:{tid}", "label": f"Columns · {tname}",
                         "kind": "delta", "before": None, "after": " · ".join(parts)})
        changed = [n for n in a_entries if n in b_entries
                   and _src_signature(a_entries[n]) != _src_signature(b_entries[n])]
        if changed:
            rows.append({"key": f"srcdef:{tid}", "label": f"Column definitions · {tname}",
                         "kind": "delta", "before": None,
                         "after": ", ".join(f"~{n}" for n in changed)})
    return rows


def _table_ids_rows(b: dict, a: dict, action: str) -> list[dict]:
    bt, at = set(b.get("tableIds") or []), set(a.get("tableIds") or [])
    label = "Tables on canvas"
    if action == "created":
        return [{"key": "tableIds", "label": label, "before": None,
                 "after": _n(len(at), "table")}] if at else []
    if action == "deleted":
        return [{"key": "tableIds", "label": label,
                 "before": _n(len(bt), "table"), "after": None}] if bt else []
    added, removed = len(at - bt), len(bt - at)
    if not added and not removed:
        return []
    parts = ([f"+{added} added"] if added else []) + ([f"−{removed} removed"] if removed else [])
    return [{"key": "tableIds", "label": label, "kind": "delta",
             "before": None, "after": " · ".join(parts)}]


# ── Núcleo ─────────────────────────────────────────────────────────────────


def _field_rows(collection: str, before: dict | None, after: dict | None,
                action: str, res: dict) -> list[dict]:
    b, a = before or {}, after or {}
    labels = dict(FIELDS.get(collection, ()))
    ordered = [k for k, _ in FIELDS.get(collection, ())]
    noise = NOISE | NOISE_BY_COLLECTION.get(collection, set())
    keys = ordered + sorted(k for k in (set(b) | set(a))
                            if k not in ordered and k not in noise and k not in _SPECIAL)
    rows: list[dict] = []
    for key in keys:
        if key in _SPECIAL or key in noise:
            continue
        bv_raw, av_raw = _clean(b.get(key)), _clean(a.get(key))
        if action == "modified":
            if _eq(bv_raw, av_raw):
                continue
            bv, av = _resolve(key, bv_raw, res), _resolve(key, av_raw, res)
        elif action == "created":
            if _skippable(av_raw):
                continue
            bv, av = None, _resolve(key, av_raw, res)
        else:  # deleted
            if _skippable(bv_raw):
                continue
            bv, av = _resolve(key, bv_raw, res), None
        rows.append({"key": key, "label": labels.get(key) or _prettify(key),
                     "before": bv, "after": av})
    rows += _udp_rows(b, a, action, res)
    if collection == "views":
        rows += _view_source_rows(b, a, action, res)
    if collection == "relationships":
        rows += _pair_rows(b, a, action, res)
    if collection == "subject_areas":
        rows += _table_ids_rows(b, a, action)
    return rows


def _display_name(collection: str, before: dict | None, after: dict | None,
                  res: dict) -> str | None:
    doc = after or before or {}
    if collection == "relationships":
        t = res.get("tables", {})
        p = doc.get("parentTableId") or doc.get("targetTableId")
        c = doc.get("childTableId") or doc.get("sourceTableId")
        if p or c:
            return f"{t.get(p, p or '?')} → {t.get(c, c or '?')}"
        return None
    return doc.get("physicalName") or doc.get("logicalName") or doc.get("name")


def entity_detail(collection: str, entity_id: str, change: dict,
                  published_before: dict | None, res: dict) -> dict:
    """UN cambio → {action, name, fields}. `change` = entrada de changes_map
    ({op, payload?, at, before?, beforeAt?}). El `before` manda: histórico si
    está estampado (changeset ya aplicado — el publicado vivo ya avanzó), o el
    doc publicado vivo (revisión en curso)."""
    op = change.get("op")
    before = _norm(change.get("before") if change.get("beforeAt") else published_before)
    after = _norm(change.get("payload")) if op != "delete" else None
    action = "deleted" if op == "delete" else ("created" if before is None else "modified")
    return {
        "collection": collection,
        "entityId": entity_id,
        "action": action,
        "name": _display_name(collection, before, after, res),
        "fields": _field_rows(collection, before, after, action, res),
    }


def collect_ref_ids(entries: list[tuple[str, dict | None, dict | None]]) -> tuple[set[str], set[str]]:
    """(tableIds, columnIds) referenciados por relaciones/vistas de los items
    (payload Y before) — para resolverlos a NOMBRE, incluso si la entidad
    referida también es nueva en este mismo changeset."""
    tids: set[str] = set()
    cids: set[str] = set()
    for collection, before, after in entries:
        for doc in (before, after):
            if not doc:
                continue
            if collection == "relationships":
                for k in ("parentTableId", "childTableId", "sourceTableId", "targetTableId"):
                    if doc.get(k):
                        tids.add(doc[k])
                for p in doc.get("pairs") or []:
                    for k in ("parentColumnId", "childColumnId"):
                        if p.get(k):
                            cids.add(p[k])
            elif collection == "views":
                for tid in doc.get("sourceTableIds") or []:
                    tids.add(tid)
                if doc.get("tableId"):
                    tids.add(doc["tableId"])
                for s in doc.get("sources") or []:
                    if s.get("tableId"):
                        tids.add(s["tableId"])
    return tids, cids
