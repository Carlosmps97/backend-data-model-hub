"""Output settings del Export DDL (doc 93 D1): CÓMO se escribe todo DDL y cómo
se nombran los archivos del zip — dato del PROYECTO (`ddl_ruleset_config.output`),
versionado en Data Standards como los lookups. El modal Export DDL arranca desde
estos valores (se pueden ajustar para un export puntual). PURO. Espejo de
`web-data-model-hub/src/features/ddl/outputSettings.ts`.

Doc 101: `dialect` = con qué dialecto abre el modal (Databricks por default) y
`oracle` = la configuración PROPIA de Oracle (las claves planas de siempre son
las de Databricks; `fileNames` es de los dos). El backend solo la guarda y la
valida: el DDL Oracle lo genera el front (`src/features/ddl/oracle.ts`).

Doc 106: el export Oracle sale con los tipos TAL CUAL están en el modelo (solo el
casing): ya no hay mapeo de tipos (`typeMap`) ni tamaño máximo de strings
(`maxStringSize`). Si una versión anterior los dejó guardados, se ignoran."""
from __future__ import annotations

import copy

DIALECTS: tuple[str, ...] = ("databricks", "oracle")

# Doc 101 §4.1: defaults de Oracle = lo pedido por el owner para el proyecto RDV
# (comentarios OFF, PK/FK ON, tipos en MAYÚSCULA) + identificadores en
# MAYÚSCULA (convención de Oracle). Sin esquema por default = nombre sin calificar.
ORACLE_DEFAULTS: dict = {
    "includeViews": True, "includeKeys": True, "includeComments": False,
    "identifierCase": "upper", "quoteIdentifiers": "when-needed", "typeCase": "upper",
    "defaultSchema": "",
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
}
_ORACLE_BOOLS = ("includeViews", "includeKeys", "includeComments")
_ORACLE_TEXTS = ("defaultSchema",)


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
