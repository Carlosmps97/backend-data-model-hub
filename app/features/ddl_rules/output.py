"""Output settings del Export DDL (doc 93 D1): CÓMO se escribe todo DDL y cómo
se nombran los archivos del zip — dato del PROYECTO (`ddl_ruleset_config.output`),
versionado en Data Standards como los lookups. El modal Export DDL arranca desde
estos valores (se pueden ajustar para un export puntual). PURO. Espejo de
`web-data-model-hub/src/features/ddl/outputSettings.ts`.

Doc 101: `dialect` = con qué dialecto abre el modal (Databricks por default) y
`oracle` = la configuración PROPIA de Oracle (las claves planas de siempre son
las de Databricks; `fileNames` es de los dos). El backend solo la guarda y la
valida: el DDL Oracle lo genera el front (`src/features/ddl/oracle.ts`)."""
from __future__ import annotations

import copy
import re

DIALECTS: tuple[str, ...] = ("databricks", "oracle")

# Doc 101 §4.3: tipos del catálogo de la plataforma que NO existen en Oracle (o
# que Oracle no acepta sin largo) → su equivalente. Origen con paréntesis = tipo
# exacto; sin paréntesis = tipo base. El largo del destino es el de las columnas
# SIN largo; una columna CON largo lo conserva si el destino lo admite (y lo
# pierde si no: DATETIME(3) → DATE). Espejo de `DEFAULT_ORACLE_TYPE_MAP` del front.
ORACLE_TYPE_MAP_DEFAULT: list[dict] = [
    {"from": "DATETIME", "to": "DATE"},
    {"from": "STRING", "to": "VARCHAR2(4000)"},
    {"from": "VARCHAR", "to": "VARCHAR(4000)"},
    {"from": "VARCHAR2", "to": "VARCHAR2(4000)"},
    {"from": "VARCHAR(MAX)", "to": "CLOB"},
    {"from": "NVARCHAR", "to": "NVARCHAR2(2000)"},
    {"from": "NVARCHAR(MAX)", "to": "NCLOB"},
    {"from": "TEXT", "to": "CLOB"},
    {"from": "DECIMAL", "to": "DECIMAL(38,18)"},
    {"from": "NUMERIC", "to": "NUMERIC(38,18)"},
    {"from": "BIGINT", "to": "NUMBER(19)"},
    {"from": "TINYINT", "to": "NUMBER(3)"},
    {"from": "DOUBLE", "to": "BINARY_DOUBLE"},
    {"from": "MONEY", "to": "NUMBER(19,4)"},
    {"from": "BOOLEAN", "to": "NUMBER(1)"},
    {"from": "TIMESTAMPTZ", "to": "TIMESTAMP WITH TIME ZONE"},
    {"from": "TIME", "to": "DATE"},
    {"from": "BINARY", "to": "BLOB"},
    {"from": "VARBINARY", "to": "RAW(2000)"},
    {"from": "VARBINARY(MAX)", "to": "BLOB"},
    {"from": "BYTES", "to": "BLOB"},
    {"from": "JSON", "to": "CLOB"},
    {"from": "JSONB", "to": "CLOB"},
    {"from": "VARIANT", "to": "CLOB"},
    {"from": "XML", "to": "XMLTYPE"},
    {"from": "UUID", "to": "VARCHAR2(36)"},
    {"from": "ARRAY", "to": "CLOB"},
    {"from": "MAP", "to": "CLOB"},
    {"from": "STRUCT", "to": "CLOB"},
]

# Doc 101 §4.1: defaults de Oracle = lo pedido por el owner para el proyecto RDV
# (comentarios OFF, PK/FK ON, tipos en MAYÚSCULA) + identificadores en
# MAYÚSCULA (convención de Oracle). Sin esquema por default = nombre sin calificar.
# `maxStringSize` = el MAX_STRING_SIZE de la base destino: con `standard` un
# VARCHAR2 de más de 4 000 (NVARCHAR2 2 000, RAW 2 000) sale CLOB/NCLOB/BLOB.
ORACLE_DEFAULTS: dict = {
    "includeViews": True, "includeKeys": True, "includeComments": False,
    "identifierCase": "upper", "quoteIdentifiers": "when-needed", "typeCase": "upper",
    "maxStringSize": "standard",
    "defaultSchema": "",
    "typeMap": copy.deepcopy(ORACLE_TYPE_MAP_DEFAULT),
}

# Defaults = semilla = convenciones de la macro BCP (doc 93 §4 D1).
OUTPUT_DEFAULTS: dict = {
    "includeViews": True, "includeKeys": False, "includeComments": False,
    "includePartitions": True, "includeIndexes": False,
    "identifierCase": "lower", "quoteIdentifiers": "when-needed", "typeCase": "lower",
    "createTable": "or-replace", "viewTagsAs": "table",
    "tableFormat": "delta", "unityCatalog": False, "catalog": "main", "defaultSchema": "default",
    "external": True,
    "location": "abfss://<container_name>@<storage_account_name>.dfs.core.windows.net/<path>",
    "locationFolderCase": "upper", "tblProperties": [],
    "fileNames": {"table": "TABLE", "modeledView": "VIEW_NEG", "generatedView": "VIEW_TEC",
                  "separator": "-", "nameCase": "upper", "schema": "when-needed"},
    "dialect": "databricks",
    "oracle": copy.deepcopy(ORACLE_DEFAULTS),
}

_ENUMS: dict[str, tuple[str, ...]] = {
    "identifierCase": ("as-is", "lower", "upper"),
    "quoteIdentifiers": ("when-needed", "always"),
    "typeCase": ("lower", "upper"),
    "createTable": ("or-replace", "if-not-exists"),
    "viewTagsAs": ("table", "view"),
    "tableFormat": ("delta", "parquet", "avro", "orc", "csv", "json"),
    "locationFolderCase": ("as-is", "lower", "upper"),
    "dialect": DIALECTS,
}
_BOOLS = ("includeViews", "includeKeys", "includeComments", "includePartitions",
          "includeIndexes", "unityCatalog", "external")
_TEXTS = ("catalog", "defaultSchema", "location")
_FILE_ENUMS: dict[str, tuple[str, ...]] = {"nameCase": ("upper", "lower", "as-is"),
                                           "schema": ("when-needed", "always")}
_FILE_TEXTS = ("table", "modeledView", "generatedView", "separator")
_FILE_ILLEGAL = set('\\/:*?"<>|')

# Configuración de Oracle (doc 101): solo lo que existe en Oracle.
_ORACLE_ENUMS: dict[str, tuple[str, ...]] = {
    "identifierCase": ("as-is", "lower", "upper"),
    "quoteIdentifiers": ("when-needed", "always"),
    "typeCase": ("lower", "upper"),
    "maxStringSize": ("standard", "extended"),
}
_ORACLE_BOOLS = ("includeViews", "includeKeys", "includeComments")
_ORACLE_TEXTS = ("defaultSchema",)
# Un tipo de dato del mapeo: letras, dígitos, espacios y la puntuación de los
# tipos (`NUMBER(19,4)`, `NUMBER(*,2)`, `NUMBER(5,-2)`, `SYS.XMLTYPE`,
# `TIMESTAMP WITH TIME ZONE`, `ARRAY<STRING>`). Sale TAL CUAL en el DDL, así que
# nada de `;`, comillas, saltos de línea ni `--` (comentario SQL).
_TYPE_TEXT = re.compile(r"^[A-Za-z][A-Za-z0-9_ (),<>:*.\-]*$")
_TYPE_MAX = 100
# Etiquetas de las cajas del formulario (Data Standards → Output settings).
_SIDE_LABEL = {"from": "Model type", "to": "Oracle type"}


def _file_names(raw) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Output setting 'fileNames' must be an object.")
    out: dict = {}
    for k, v in raw.items():
        if k in _FILE_ENUMS:
            if v not in _FILE_ENUMS[k]:
                raise ValueError(f"File names '{k}' must be one of: {', '.join(_FILE_ENUMS[k])}.")
            out[k] = v
        elif k in _FILE_TEXTS:
            text = str(v if v is not None else "").strip()
            if any(ch in _FILE_ILLEGAL for ch in text):
                raise ValueError(f"File names '{k}' can't contain \\ / : * ? \" < > |.")
            out[k] = text
    return out


def _type_text(value, row: int, side: str) -> str:
    text = re.sub(r"\s+", " ", str(value if value is not None else "")).strip()
    if not text:
        return ""
    if len(text) > _TYPE_MAX:
        raise ValueError(f"Oracle type mapping, row {row}: the {_SIDE_LABEL[side]} is longer than "
                         f"{_TYPE_MAX} characters.")
    if "--" in text or not _TYPE_TEXT.match(text):
        kind = "Oracle type" if side == "to" else "data type"
        raise ValueError(f"Oracle type mapping, row {row}: '{text[:40]}' isn't a valid {kind}.")
    return text


def _type_map(raw) -> list[dict]:
    """Filas {from, to} (fila = posición en la lista, como la ve el usuario): se
    recortan; una fila vacía se descarta; un lado sin el otro, un texto que no es
    un tipo o un origen repetido (sin distinguir mayúsculas ni espacios, como
    compara el generador — ganaría siempre la primera) → ValueError."""
    if not isinstance(raw, list) or any(not isinstance(p, dict) for p in raw):
        raise ValueError("Oracle setting 'typeMap' must be a list of {from, to}.")
    out: list[dict] = []
    first_row: dict[str, tuple[int, str]] = {}
    for row, p in enumerate(raw, start=1):
        src, dst = _type_text(p.get("from"), row, "from"), _type_text(p.get("to"), row, "to")
        if not src and not dst:
            continue
        if not src or not dst:
            missing = _SIDE_LABEL["from" if not src else "to"]
            raise ValueError(f"Oracle type mapping, row {row}: the {missing} is missing.")
        key = re.sub(r"\s+", "", src).upper()
        if key in first_row:
            prev_row, prev_text = first_row[key]
            raise ValueError(f"Oracle type mapping: '{prev_text}' is mapped more than once "
                             f"(rows {prev_row} and {row}).")
        first_row[key] = (row, src)
        out.append({"from": src, "to": dst})
    return out


def _oracle(raw) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Output setting 'oracle' must be an object.")
    out: dict = {}
    for k, v in raw.items():
        if k in _ORACLE_ENUMS:
            if v not in _ORACLE_ENUMS[k]:
                raise ValueError(f"Oracle setting '{k}' must be one of: {', '.join(_ORACLE_ENUMS[k])}.")
            out[k] = v
        elif k in _ORACLE_BOOLS:
            if not isinstance(v, bool):
                raise ValueError(f"Oracle setting '{k}' must be true or false.")
            out[k] = v
        elif k in _ORACLE_TEXTS:
            if not isinstance(v, str):
                raise ValueError(f"Oracle setting '{k}' must be text.")
            out[k] = v.strip()
        elif k == "typeMap":
            out[k] = _type_map(v)
    return out


def normalize_output(raw: dict | None) -> dict:
    """Limpia el bloque `output` que llega al apply: claves conocidas con valores
    permitidos; las desconocidas se descartan. Un valor inválido levanta
    ValueError con un mensaje en inglés (va al toast del front)."""
    out: dict = {}
    for k, v in (raw or {}).items():
        if k in _ENUMS:
            if v not in _ENUMS[k]:
                raise ValueError(f"Output setting '{k}' must be one of: {', '.join(_ENUMS[k])}.")
            out[k] = v
        elif k in _BOOLS:
            if not isinstance(v, bool):
                raise ValueError(f"Output setting '{k}' must be true or false.")
            out[k] = v
        elif k in _TEXTS:
            if not isinstance(v, str):
                raise ValueError(f"Output setting '{k}' must be text.")
            out[k] = v.strip()
        elif k == "tblProperties":
            if not isinstance(v, list) or any(not isinstance(p, dict) for p in v):
                raise ValueError("Output setting 'tblProperties' must be a list of {key, value}.")
            out[k] = [{"key": str(p.get("key") or "").strip(),
                       "value": str(p.get("value") if p.get("value") is not None else "")}
                      for p in v if str(p.get("key") or "").strip()]
        elif k == "fileNames":
            out[k] = _file_names(v)
        elif k == "oracle":
            out[k] = _oracle(v)
    return out


def _effective_oracle(raw) -> dict:
    """Defaults de Oracle + lo guardado, clave a clave (un valor viejo inválido
    cae a su default sin descartar los demás)."""
    eff = copy.deepcopy(ORACLE_DEFAULTS)
    if not isinstance(raw, dict):
        return eff
    for k, v in raw.items():
        try:
            eff.update(_oracle({k: v}))
        except ValueError:
            continue
    return eff


def effective_output(raw: dict | None) -> dict:
    """Defaults + lo guardado en el proyecto. Tolerante CLAVE A CLAVE: un valor
    guardado inválido (dato viejo) cae a su default sin descartar los demás."""
    eff = copy.deepcopy(OUTPUT_DEFAULTS)
    for k, v in (raw or {}).items():
        if k == "oracle":
            eff["oracle"] = _effective_oracle(v)
            continue
        try:
            clean = normalize_output({k: v})
        except ValueError:
            continue
        if "fileNames" in clean:
            eff["fileNames"] = {**eff["fileNames"], **clean.pop("fileNames")}
        eff.update(clean)
    return eff
