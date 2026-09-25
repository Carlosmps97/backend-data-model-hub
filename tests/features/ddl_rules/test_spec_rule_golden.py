"""Doc 93 · Anexo A — el caso del owner (`spec-rule.md`): tabla DAC
`bcp_udv_int.H_SALDOCIERREPROCESOBANCAMINORISTA` + 2 vistas `_vu` (con y sin
columnas DAC). La base (CREATE físico y vistas) es EXACTAMENTE lo que emite el
front (misma cadena en `generators.test.ts`); el motor corre la semilla real.
Cada objeto se compara byte a byte como queda en su archivo del zip
(statement + anexos unidos por salto de línea)."""
from __future__ import annotations

from app.features.ddl_rules.engine import pipeline
from app.features.ddl_rules.templates import SEED_LOOKUPS, SEED_RULES

DEFS = [
    {"id": "u-dac-col", "name": "Clasificacion del Dato", "level": "column", "view": "physical", "dataType": "list",
     "allowedValues": ["No Definido", "No DAC", "DAC-CUENTA", "DAC-GLOSADAC"]},
    {"id": "u-dac-tab", "name": "Clasificacion del Dato", "level": "table", "view": "physical", "dataType": "list",
     "allowedValues": ["No Definido", "No DAC", "DAC"]},
    {"id": "u-vac", "name": "Frecuencia Vacuum", "level": "table", "view": "physical", "dataType": "list",
     "allowedValues": list(SEED_LOOKUPS["vacuum_map"]["values"])},
    {"id": "u-part", "name": "Particion", "level": "column", "view": "physical", "dataType": "list",
     "allowedValues": ["No Definido", "PART_01", "PART_02", "PART_03"]},
]
_ID = {(d["level"], d["name"]): d["id"] for d in DEFS}
CONFIG = {"lookups": {n: {**{k: v for k, v in lk.items() if k != "fromUdpName"},
                          "fromUdpId": _ID[(lk["fromLevel"], lk["fromUdpName"])]}
                      for n, lk in SEED_LOOKUPS.items()}, "functions": []}
RULES = [{**r, "id": f"seed-{i}", "validationState": "valid"} for i, r in enumerate(SEED_RULES)]

# (columna, tipo del modelo, ddlType que manda el front, Clasificacion del Dato, Particion)
ROWS = [
    ("CODCLAVEPARTYCLI", "VARCHAR(128)", "varchar(128)", "No DAC", "No Definido"),
    ("TIPROLCLI", "CHAR(20)", "char(20)", "No DAC", "No Definido"),
    ("CODCLAVECTA", "VARCHAR(128)", "varchar(128)", "No DAC", "No Definido"),
    ("CODPRODUCTO", "VARCHAR(30)", "varchar(30)", "No DAC", "No Definido"),
    ("NBRGRUPOPROCESOBCAMINORISTA", "VARCHAR(120)", "varchar(120)", "No DAC", "PART_02"),
    ("CODMES", "INTEGER", "int", "No DAC", "PART_01"),
    ("CODCLAVEUNICOCLI", "VARCHAR(128)", "varchar(128)", "No DAC", "No Definido"),
    ("CODINTERNOCOMPUTACIONAL", "VARCHAR(30)", "varchar(30)", "No DAC", "No Definido"),
    ("CODCTAAPP", "STRING", "string", "DAC-CUENTA", "No Definido"),
    ("CODCTA20", "STRING", "string", "DAC-CUENTA", "No Definido"),
    ("CODCTACOMERCIAL", "STRING", "string", "DAC-CUENTA", "No Definido"),
    ("CODCTAINTERNA", "STRING", "string", "DAC-CUENTA", "No Definido"),
    ("CODCLAVEPRODUCTO", "VARCHAR(128)", "varchar(128)", "No DAC", "No Definido"),
    ("DESPRODUCTO", "VARCHAR(256)", "varchar(256)", "No DAC", "No Definido"),
    ("CODLLAVESECTORFIN", "INTEGER", "int", "No DAC", "No Definido"),
    ("CODSECTORFIN", "VARCHAR(30)", "varchar(30)", "No DAC", "No Definido"),
    ("DESSECTORFIN", "VARCHAR(256)", "varchar(256)", "No DAC", "No Definido"),
    ("DESGLOSACIERREPROCESO", "STRING", "string", "DAC-GLOSADAC", "No Definido"),
    ("MTOSALDOMEDIOVIGSOL", "DECIMAL(21,4)", "decimal(21,4)", "No DAC", "No Definido"),
    ("MTOSALDOMEDIOVIGDOL", "DECIMAL(21,4)", "decimal(21,4)", "No DAC", "No Definido"),
    ("MTOSALDOFINMESVIGSOL", "DECIMAL(21,4)", "decimal(21,4)", "No DAC", "No Definido"),
    ("MTOSALDOFINMESVIGDOL", "DECIMAL(21,4)", "decimal(21,4)", "No DAC", "No Definido"),
    ("FECRUTINA", "DATE", "date", "No DAC", "No Definido"),
    ("FECACTUALIZACIONREGISTRO", "TIMESTAMP", "timestamp", "No DAC", "No Definido"),
    ("TIPFRECUENCIAREGISTRO", "CHAR(1)", "char(1)", "No DAC", "No Definido"),
    ("FECDIA", "DATE", "date", "No DAC", "No Definido"),
]
NAMES = [r[0].lower() for r in ROWS]
DAC_SUFFIX = {r[0].lower(): r[3].split("-", 1)[1] for r in ROWS if r[3].startswith("DAC-")}
COLS = [{"physicalName": n, "dataType": t, "ddlType": dt, "ordinal": i, "isPrimaryKey": i < 6,
         "isNullable": i >= 6, "isPartition": p.startswith("PART_"),
         "udpValues": {"u-dac-col": dac, "u-part": p}}
        for i, (n, t, dt, dac, p) in enumerate(ROWS)]
PHYS = "H_SALDOCIERREPROCESOBANCAMINORISTA"
TABLE = {"id": "t1", "physicalName": PHYS, "schema": "bcp_udv_int",
         "udpValues": {"u-dac-tab": "DAC", "u-vac": "MONTHLY_90 days", "u-carga": "Tipo 4 (Historia Snapshot)"}}
LOC = "abfss://<container_name>@<storage_account_name>.dfs.core.windows.net/<path>"
OPTIONS = {"identifierCase": "lower", "quoteIdentifiers": "when-needed", "typeCase": "lower",
           "createTable": "or-replace", "viewTagsAs": "table", "includeKeys": False, "tableFormat": "delta",
           "external": True, "location": LOC, "locationFolderCase": "upper", "includePartitions": True,
           "partitionsLast": True}

# ── Base emitida por el FRONT (misma cadena en generators.test.ts) ─────────
SPEC_BASE_SQL = (
    "CREATE OR REPLACE TABLE bcp_udv_int.h_saldocierreprocesobancaminorista (\n"
    + ",\n".join(f"  {r[0].lower()} {r[2]}" for r in ROWS if not r[4].startswith("PART_"))
    + ",\n  codmes int,\n  nbrgrupoprocesobcaminorista varchar(120)\n)\nUSING DELTA\n"
    "PARTITIONED BY (codmes, nbrgrupoprocesobcaminorista)\n"
    f"LOCATION '{LOC}/H_SALDOCIERREPROCESOBANCAMINORISTA';")
NON_DAC = [n for n in NAMES if n not in DAC_SUFFIX]


def _plain_view(full: str, names: list[str], src: str) -> str:
    return (f"CREATE OR REPLACE VIEW {full} AS\nSELECT\n  "
            + ",\n  ".join(f"{n} AS {n}" for n in names) + f"\nFROM {src};")


SRC = "bcp_udv_int.h_saldocierreprocesobancaminorista"
VU_DAC_SQL = _plain_view("bcp_udv_int_vu.h_saldocierreprocesobancaminoristadac", NAMES, SRC)
VU_SQL = _plain_view("bcp_udv_int_vu.h_saldocierreprocesobancaminorista", NON_DAC, SRC)
PAYLOAD = {"model": {"name": "UDV INT", "udpValues": {}}, "options": OPTIONS,
           "tables": [{"table": TABLE, "columns": COLS, "baseSql": SPEC_BASE_SQL}],
           "views": [{"name": "H_SALDOCIERREPROCESOBANCAMINORISTADAC", "schema": "bcp_udv_int_vu",
                      "sql": VU_DAC_SQL, "sourceTableIds": ["t1"], "businessView": True},
                     {"name": "H_SALDOCIERREPROCESOBANCAMINORISTA", "schema": "bcp_udv_int_vu",
                      "sql": VU_SQL, "sourceTableIds": ["t1"], "businessView": True}]}

# ── Esperado (Anexo A del doc 93) ─────────────────────────────────────────
TBLPROPS = ("TBLPROPERTIES (\n  'delta.deletedFileRetentionDuration' = '90 days',\n"
            "  'delta.logRetentionDuration' = '90 days'\n);")


def _tags(full: str, is_dac: str, dac_cols: list[str]) -> str:
    lines = [f"ALTER TABLE {full} SET TAGS ('updateFrequency' = 'MONTHLY');",
             f"ALTER TABLE {full} SET TAGS ('isDAC' = '{is_dac}');"]
    lines += [f"ALTER TABLE {full} ALTER COLUMN {c} SET TAGS ('DAC' = '{DAC_SUFFIX[c]}');" for c in dac_cols]
    return "\n".join(lines)


def _proj(name: str, decrypt: bool) -> str:
    if decrypt and name in DAC_SUFFIX:
        return f"bcp_encrypt_function.decrypt_column_view({name}, '{DAC_SUFFIX[name]}') AS {name}"
    return f"{name} AS {name}"


def _view(full: str, names: list[str], src: str, decrypt: bool, tail: tuple[str, ...] = ()) -> str:
    items = [_proj(n, decrypt) for n in names] + [f"{t} AS {t}" for t in tail]
    return f"CREATE OR REPLACE VIEW {full} AS\nSELECT\n  " + ",\n  ".join(items) + f"\nFROM {src};"


DAC_COLS = [n for n in NAMES if n in DAC_SUFFIX]
FISICA = ("-- DROP TABLE IF EXISTS bcp_udv_int.h_saldocierreprocesobancaminorista;\n"
          + SPEC_BASE_SQL[:-1].replace("  tiprolcli char(20)", "  tiprolcli varchar(20)")
                              .replace("  tipfrecuenciaregistro char(1)", "  tipfrecuenciaregistro varchar(1)")
          + "\n" + TBLPROPS + "\n" + _tags(SRC, "True", DAC_COLS))
REJ_FULL = "bcp_udv_int.h_saldocierreprocesobancaminorista_rej"
REJ = ("-- DROP TABLE IF EXISTS " + REJ_FULL + ";\n"
       "CREATE OR REPLACE TABLE " + REJ_FULL + " (\n"
       + ",\n".join(f"  {n} string" for n in NAMES if n not in ("codmes", "nbrgrupoprocesobcaminorista"))
       + ",\n  tiporeject string,\n  codmes int,\n  nbrgrupoprocesobcaminorista varchar(120)\n)\nUSING DELTA\n"
       "PARTITIONED BY (codmes, nbrgrupoprocesobcaminorista)\n"
       f"LOCATION '{LOC}/H_SALDOCIERREPROCESOBANCAMINORISTA_REJ'\n" + TBLPROPS + "\n"
       + _tags(REJ_FULL, "True", DAC_COLS))
V = "bcp_udv_int_v.h_saldocierreprocesobancaminorista"
VU_DAC_FULL = "bcp_udv_int_vu.h_saldocierreprocesobancaminoristadac"
EXPECTED = {
    ("bcp_udv_int", PHYS): FISICA,
    ("bcp_udv_int", f"{PHYS}_rej"): REJ,
    ("bcp_udv_int_v", PHYS): _view(V, NON_DAC, SRC, False) + "\n" + _tags(V, "False", []),
    ("bcp_udv_int_v", f"{PHYS}dac"): (_view(f"{V}dac", NAMES, SRC, True) + "\n"
                                      + _tags(f"{V}dac", "True", DAC_COLS)),
    ("bcp_udv_int_v", f"{PHYS}_rej"): (_view(f"{V}_rej", NON_DAC, REJ_FULL, False, ("tiporeject",)) + "\n"
                                       + _tags(f"{V}_rej", "False", [])),
    ("bcp_udv_int_v", f"{PHYS}dac_rej"): (_view(f"{V}dac_rej", NAMES, REJ_FULL, True, ("tiporeject",)) + "\n"
                                          + _tags(f"{V}dac_rej", "True", DAC_COLS)),
    ("bcp_udv_int_vu", f"{PHYS}DAC"): (_view(VU_DAC_FULL, NAMES, SRC, True) + "\n"
                                       + _tags(VU_DAC_FULL, "True", DAC_COLS)),
    ("bcp_udv_int_vu", PHYS): VU_SQL + "\n" + _tags("bcp_udv_int_vu.h_saldocierreprocesobancaminorista", "False", []),
}


def _files(out) -> dict[tuple[str, str], str]:
    """Contenido por objeto tal como queda en su archivo: statement + anexos."""
    objs: dict[tuple[str, str], list[str]] = {}
    for s in out["statements"]:
        objs.setdefault((s["schema"], s["name"]), []).append(s["sql"])
    return {k: "\n".join(v) for k, v in objs.items()}


def test_anexo_a_tabla_dac_byte_a_byte():
    files = _files(pipeline.render_export(PAYLOAD, RULES, CONFIG, DEFS, {}))
    assert set(files) == set(EXPECTED)
    for key, expected in EXPECTED.items():
        assert files[key] == expected, key


def test_tabla_no_dac_sin_objetos_dac():
    table = {**TABLE, "udpValues": {**TABLE["udpValues"], "u-dac-tab": "No DAC"}}
    payload = {**PAYLOAD, "tables": [{"table": table, "columns": COLS, "baseSql": SPEC_BASE_SQL}]}
    files = _files(pipeline.render_export(payload, RULES, CONFIG, DEFS, {}))
    assert set(files) == {("bcp_udv_int", PHYS), ("bcp_udv_int", f"{PHYS}_rej"), ("bcp_udv_int_v", PHYS),
                          ("bcp_udv_int_v", f"{PHYS}_rej"), ("bcp_udv_int_vu", f"{PHYS}DAC"),
                          ("bcp_udv_int_vu", PHYS)}
    # sin exclusión: la técnica lleva las 26 columnas, sin desencriptar
    assert files[("bcp_udv_int_v", PHYS)].startswith(_view(V, NAMES, SRC, False))
    assert "SET TAGS ('isDAC' = 'False');" in files[("bcp_udv_int", PHYS)]
    assert "SET TAGS ('isDAC' = 'False');" in files[("bcp_udv_int_v", PHYS)]
