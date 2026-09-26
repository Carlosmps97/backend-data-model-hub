"""Doc 95 · LA regla de la cascada de tipos de un parent domain, en un solo
lugar (pura). La usan la cascada de ida (`update_domain`), el revert de un
dominio, el rollback de Data Standards (D9), la vista previa por columna (D7) y
el restore del modelo (D10).

Una columna SIGUE al dominio en una faceta cuando está activa, apunta al
dominio, no tiene override de esa faceta y conserva el tipo que el dominio
tiene HOY. Si su tipo ya es otro (p. ej. `INT` crudo de Erwin con el dominio en
`INTEGER`) o tiene override, el contrato está «divorciado» y no se toca."""
from __future__ import annotations

# faceta → (campo de la columna, flag de override de la columna, campo del dominio)
FACETS: dict[str, tuple[str, str, str]] = {
    "physical": ("dataType", "typeOverridden", "defaultDataType"),
    "logical": ("logicalDataType", "logicalTypeOverridden", "logicalDataType"),
}
STATUSES = ("change", "override", "differs")


def retype_filter(domain_id: str, facet: str, current_type: str | None) -> dict:
    """Filtro de las columnas que SIGUEN al dominio en `facet` (las que re-tipa
    un cambio de tipo). `current_type=None` ⇒ las que tampoco tienen tipo."""
    field, flag, _ = FACETS[facet]
    return {"parentDomainId": domain_id, flag: {"$ne": True}, field: current_type,
            "flgactive": {"$ne": False}}


def column_status(col: dict, facet: str, current_type: str | None) -> str:
    """Espejo puro de `retype_filter` para UNA columna del dominio: 'change' (lo
    sigue), 'override' o 'differs' (su tipo ya es otro)."""
    field, flag, _ = FACETS[facet]
    if col.get(flag) is True:
        return "override"
    return "change" if col.get(field) == current_type else "differs"


def classify_columns(cols: list[dict], domain_types: dict[str, str | None],
                     targets: dict[str, str | None]) -> list[dict]:
    """Una fila por columna del dominio con el estado de cada faceta que CAMBIA
    (`targets`) y el de la fila: 'change' si alguna faceta cambia; si no,
    'override' si alguna tiene override; si no, 'differs'. Sin `targets` (el
    panel del dominio) mide la faceta física contra el tipo actual: ¿lo sigue?"""
    facets = list(targets) or ["physical"]
    rows: list[dict] = []
    for c in cols:
        per = {f: column_status(c, f, domain_types.get(f)) for f in facets}
        states = set(per.values())
        status = "change" if "change" in states else "override" if "override" in states else "differs"
        rows.append({
            "columnId": str(c.get("_id", c.get("id"))), "tableId": c.get("tableId"),
            "column": c.get("physicalName") or "", "attribute": c.get("logicalName") or "",
            "physicalType": c.get("dataType"), "logicalType": c.get("logicalDataType"),
            "physical": per.get("physical"), "logical": per.get("logical"), "status": status,
        })
    return rows


def _fold(s: str | None) -> str:
    return (s or "").lower()


def summarize_columns(rows: list[dict], names: dict[str, dict], *, q: str | None = None,
                      status: str | None = None, offset: int = 0, limit: int = 500) -> dict:
    """Totales GLOBALES (no dependen de q/status) + la página de filas que
    matchean `q` (tabla o columna, física o lógica) y `status`, ordenadas por
    tabla y columna. Puro: no muta `rows`."""
    decorated = []
    for r in rows:
        t = names.get(r.get("tableId")) or {}
        decorated.append({**r, "schema": t.get("schema"), "table": t.get("physicalName") or "",
                          "tableLogical": t.get("logicalName") or ""})
    change = [r for r in decorated if r["status"] == "change"]
    totals = {"columns": len(decorated), "tables": len({r["tableId"] for r in decorated}),
              "change": len(change), "changeTables": len({r["tableId"] for r in change}),
              "override": sum(1 for r in decorated if r["status"] == "override"),
              "differs": sum(1 for r in decorated if r["status"] == "differs")}
    needle = _fold(q).strip()
    hits = [r for r in decorated
            if (not status or r["status"] == status)
            and (not needle or any(needle in _fold(r.get(k)) for k in ("table", "tableLogical", "column", "attribute")))]
    hits.sort(key=lambda r: (_fold(r["table"]), _fold(r["column"]), r["columnId"]))
    return {"totals": totals, "matched": len(hits), "rows": hits[offset:offset + limit]}


def align_restored_column(payload: dict, current: dict | None,
                          domain_types: dict[str, tuple[str | None, str | None]]) -> dict:
    """Doc 95 D10: el «Restore to vN» del modelo no le devuelve a una columna un
    tipo que su dominio ya cambió. Si la columna HOY sigue a su dominio en una
    faceta (mismo dominio en el restore, sin override en el restore y con el
    tipo vigente del dominio) conserva el tipo vigente; si no, el payload queda
    tal cual. No muta `payload`."""
    domain_id = payload.get("parentDomainId")
    if not current or not domain_id or domain_id != current.get("parentDomainId"):
        return payload
    types = domain_types.get(domain_id)
    if not types:
        return payload
    out = dict(payload)
    for facet, dom_type in zip(("physical", "logical"), types):
        field, flag, _ = FACETS[facet]
        if dom_type is None or payload.get(flag) is True:
            continue
        if column_status(current, facet, dom_type) == "change":
            out[field] = dom_type
    return out
