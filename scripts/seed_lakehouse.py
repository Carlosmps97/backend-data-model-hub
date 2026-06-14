"""Lakehouse demo project — Python port of `seedModel()` from the redesign
prototype (`newStyleWeb/Redesign/app/data.js`).

Produces a fully-loaded project (layers RDV·UDV·DDV, vertical domains
Finanzas·Clientes·Riesgo, 14 tables with FK relationships and a few
role-specific SQL views) in the **new project-centric schema**.

The column-id convention (`<tableId>.<colName>`) and relationship endpoint
shape are replicated verbatim from `data.js` so the seeded data renders
identically to the prototype once the frontend is ported from the same file.

Exposed: `build_lakehouse()` → (project_doc, table_docs, relationship_docs).
Mongo wrapping (`projectId`, `flgactive`, timestamps) is added by the caller.
"""

from __future__ import annotations

from typing import Any

PROJECT_ID = "lakehouse"

# ── Layers ("Modelos / niveles") ────────────────────────────────────────────
LAYERS: list[dict[str, Any]] = [
    {"id": "rdv", "name": "RDV", "full": "Raw Data Vault", "color": "#0e7490", "order": 0,
     "engine": "databricks_sql", "desc": "Ingesta cruda alineada a la fuente."},
    {"id": "udv", "name": "UDV", "full": "Unified Data Vault", "color": "#4f46e5", "order": 1,
     "engine": "databricks_sql", "desc": "Consolidación universal multi-fuente."},
    {"id": "ddv", "name": "DDV", "full": "Dimensional Data Vault", "color": "#7c3aed", "order": 2,
     "engine": "databricks_sql", "desc": "Analítico agregado a nivel cliente."},
]

# ── Domains (vertical data products) ────────────────────────────────────────
DOMAINS: dict[str, dict[str, Any]] = {
    "finanzas": {"id": "finanzas", "name": "Finanzas", "color": "#0d9488", "owner": "M. Torres",
                 "steward": "J. Rivas", "sensitivity": "Confidential",
                 "description": "Productos financieros: tarjetas, savings y leasing; saldos y transacciones.",
                 "subdomains": ["Tarjetas", "Savings", "Leasing", "Contable", "Transaccional", "Analítico"],
                 "layers": ["rdv", "udv", "ddv"]},
    "clientes": {"id": "clientes", "name": "Clientes", "color": "#4f46e5", "owner": "P. Núñez",
                 "steward": "R. Vega", "sensitivity": "PII",
                 "description": "Maestro de clientes, atributos personales y segmentación.",
                 "subdomains": ["Maestro", "Segmentación", "Analítico"], "layers": ["rdv", "udv", "ddv"]},
    "riesgo": {"id": "riesgo", "name": "Riesgo", "color": "#b45309", "owner": "A. Díaz",
               "steward": "L. Soto", "sensitivity": "Restricted",
               "description": "Exposición crediticia y scoring de riesgo.",
               "subdomains": ["Exposición", "Scoring"], "layers": ["udv", "ddv"]},
}


# ── Builders (mirror data.js `col`, `T`, `view`) ────────────────────────────

def _col(cid: str, name: str, data_type: str, key: str = "", ref: dict | None = None) -> dict[str, Any]:
    return {
        "id": cid, "name": name, "dataType": data_type, "logicalName": "",
        "isPrimaryKey": key == "PK", "isForeignKey": key == "FK",
        "isSurrogateKey": key == "SK", "isPartitionKey": key == "PT",
        "isNullable": not (key in ("PK", "FK")),
        "functionalDefinition": "", "foreignKeyRef": ref,
    }


def _t(tid, layer, domain, subdomain, schema, name, defn, cols, views=None) -> dict[str, Any]:
    return {
        "id": tid, "name": name, "logicalName": name, "schema": schema,
        "layer": layer, "domain": domain, "subdomain": subdomain or "",
        "color": DOMAINS.get(domain, {}).get("color", "#4f46e5"),
        "functionalDefinition": defn,
        "columns": [
            _col(f"{tid}.{c[0]}", c[0], c[1], c[2] if len(c) > 2 else "",
                 c[3] if len(c) > 3 else None)
            for c in cols
        ],
        "views": views or [],
    }


def _view(vid, name, vtype, role, select, where="") -> dict[str, Any]:
    return {"id": vid, "name": name, "type": vtype, "role": role, "select": select, "where": where}


def build_lakehouse() -> tuple[dict, list[dict], list[dict]]:
    """Return (project_doc, table_docs, relationship_docs) in the new schema."""
    tables: list[dict[str, Any]] = [
        # ===== RDV =====
        _t("rdv_hub_cuenta", "rdv", "finanzas", "Contable", "rdv", "hub_cuenta",
           "Hub de cuentas financieras (clave de negocio).",
           [["cuenta_id", "bigint", "PK"], ["cuenta_bk", "varchar(32)"], ["load_date", "timestamp"]]),
        _t("rdv_sat_tc", "rdv", "finanzas", "Tarjetas", "rdv", "sat_tarjeta_credito",
           "Movimientos crudos de tarjetas de crédito (CoreCards).",
           [["cuenta_id", "bigint", "FK", {"table": "rdv_hub_cuenta", "column": "cuenta_id"}],
            ["tarjeta_num", "varchar(20)"], ["saldo", "decimal(15,2)"], ["load_date", "timestamp"]]),
        _t("rdv_sat_sav", "rdv", "finanzas", "Savings", "rdv", "sat_savings",
           "Saldos crudos de cuentas de ahorro (SavingsApp).",
           [["cuenta_id", "bigint", "FK", {"table": "rdv_hub_cuenta", "column": "cuenta_id"}],
            ["saldo", "decimal(15,2)"], ["tasa", "decimal(6,4)"], ["load_date", "timestamp"]]),
        _t("rdv_sat_lea", "rdv", "finanzas", "Leasing", "rdv", "sat_leasing",
           "Contratos de leasing crudos (LeasingApp).",
           [["cuenta_id", "bigint", "FK", {"table": "rdv_hub_cuenta", "column": "cuenta_id"}],
            ["cuota", "decimal(15,2)"], ["plazo", "smallint"], ["load_date", "timestamp"]]),
        _t("rdv_hub_cli", "rdv", "clientes", "Maestro", "rdv", "hub_cliente", "Hub de clientes.",
           [["cliente_id", "bigint", "PK"], ["cliente_bk", "varchar(32)"], ["load_date", "timestamp"]]),
        _t("rdv_sat_cli", "rdv", "clientes", "Maestro", "rdv", "sat_cliente",
           "Atributos crudos de cliente (PII).",
           [["cliente_id", "bigint", "FK", {"table": "rdv_hub_cli", "column": "cliente_id"}],
            ["nombre", "varchar(120)"], ["documento", "varchar(15)"], ["email", "varchar(160)"],
            ["load_date", "timestamp"]]),
        # ===== UDV =====
        _t("udv_h_saldo", "udv", "finanzas", "Contable", "udv", "h_saldocontable",
           "Saldos contables unificados multi-producto por cuenta.",
           [["saldo_id", "bigint", "PK"],
            ["cuenta_id", "bigint", "FK", {"table": "rdv_hub_cuenta", "column": "cuenta_id"}],
            ["cliente_id", "bigint", "FK", {"table": "rdv_hub_cli", "column": "cliente_id"}],
            ["saldo", "decimal(15,2)"], ["periodo", "date"]],
           [_view("v_saldo_b", "vw_saldo_negocio", "business", "Analista Financiero",
                  "cuenta_id\nsaldo\nperiodo", "periodo >= '2024-01-01'")]),
        _t("udv_h_trx", "udv", "finanzas", "Transaccional", "udv", "h_transacciones",
           "Transacciones unificadas de todos los productos.",
           [["trx_id", "bigint", "PK"],
            ["cuenta_id", "bigint", "FK", {"table": "rdv_hub_cuenta", "column": "cuenta_id"}],
            ["monto", "decimal(15,2)"], ["fecha", "timestamp"]]),
        _t("udv_u_cli", "udv", "clientes", "Maestro", "udv", "u_cliente", "Cliente universal consolidado.",
           [["cliente_id", "bigint", "PK"],
            ["src_cliente_id", "bigint", "FK", {"table": "rdv_hub_cli", "column": "cliente_id"}],
            ["nombre", "varchar(120)"], ["segmento", "varchar(24)"], ["email", "varchar(160)"]],
           [_view("v_cli_pii", "vw_cliente_pii", "technical", "Data Engineer",
                  "cliente_id\nsha256(CAST(email AS varchar)) AS email_hash\nsegmento"),
            _view("v_cli_b", "vw_cliente_negocio", "business", "Analista", "cliente_id\nsegmento")]),
        _t("udv_h_expo", "udv", "riesgo", "Exposición", "udv", "h_exposicion",
           "Exposición crediticia consolidada por cliente.",
           [["expo_id", "bigint", "PK"],
            ["cliente_id", "bigint", "FK", {"table": "rdv_hub_cli", "column": "cliente_id"}],
            ["exposicion", "decimal(18,2)"], ["periodo", "date"]]),
        # ===== DDV =====
        _t("ddv_d_cli", "ddv", "clientes", "Analítico", "ddv", "d_cliente",
           "Dimensión cliente para analítica.",
           [["cliente_id", "bigint", "PK"],
            ["src_cliente_id", "bigint", "FK", {"table": "udv_u_cli", "column": "cliente_id"}],
            ["nombre", "varchar(120)"], ["segmento", "varchar(24)"]]),
        _t("ddv_f_smedio", "ddv", "finanzas", "Analítico", "ddv", "f_saldomedio",
           "Saldo medio mensual a nivel cliente.",
           [["id", "bigint", "PK"],
            ["cliente_id", "bigint", "FK", {"table": "ddv_d_cli", "column": "cliente_id"}],
            ["saldo_id", "bigint", "FK", {"table": "udv_h_saldo", "column": "saldo_id"}],
            ["saldo_medio", "decimal(15,2)"], ["periodo", "date"]]),
        _t("ddv_f_base", "ddv", "finanzas", "Analítico", "ddv", "f_basecontable",
           "Base contable agregada a nivel cliente.",
           [["id", "bigint", "PK"],
            ["cliente_id", "bigint", "FK", {"table": "ddv_d_cli", "column": "cliente_id"}],
            ["activos", "decimal(18,2)"], ["pasivos", "decimal(18,2)"], ["periodo", "date"]]),
        _t("ddv_f_score", "ddv", "riesgo", "Scoring", "ddv", "f_score_credito", "Score crediticio por cliente.",
           [["id", "bigint", "PK"],
            ["cliente_id", "bigint", "FK", {"table": "ddv_d_cli", "column": "cliente_id"}],
            ["expo_id", "bigint", "FK", {"table": "udv_h_expo", "column": "expo_id"}],
            ["score", "smallint"], ["periodo", "date"]]),
    ]

    # Relationships derived from FK columns (mirror data.js lines 98-103).
    relationships: list[dict[str, Any]] = []
    for t in tables:
        for c in t["columns"]:
            if c["isForeignKey"] and c["foreignKeyRef"]:
                ref = c["foreignKeyRef"]
                relationships.append({
                    "id": f"r_{t['id']}_{c['name']}",
                    "source": {"table": t["id"], "column": c["id"]},
                    "target": {"table": ref["table"], "column": f"{ref['table']}.{ref['column']}"},
                    "cardinality": "1:N",
                })

    project = {
        "id": PROJECT_ID,
        "name": "Lakehouse",
        "description": "Repositorio de datos centralizado de la organización — capas RDV · UDV · DDV.",
        "engines": ["databricks_sql"],
        "layers": LAYERS,
        "domains": list(DOMAINS.values()),
    }
    return project, tables, relationships
