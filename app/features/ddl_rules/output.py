"""Output settings del Export DDL (doc 93 D1): CÓMO se escribe todo DDL y cómo
se nombran los archivos del zip — dato del PROYECTO (`ddl_ruleset_config.output`),
versionado en Data Standards como los lookups. El modal Export DDL arranca desde
estos valores (se pueden ajustar para un export puntual). PURO. Espejo de
`web-data-model-hub/src/features/ddl/outputSettings.ts`."""
from __future__ import annotations

import copy

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
}

_ENUMS: dict[str, tuple[str, ...]] = {
    "identifierCase": ("as-is", "lower", "upper"),
    "quoteIdentifiers": ("when-needed", "always"),
    "typeCase": ("lower", "upper"),
    "createTable": ("or-replace", "if-not-exists"),
    "viewTagsAs": ("table", "view"),
    "tableFormat": ("delta", "parquet", "avro", "orc", "csv", "json"),
    "locationFolderCase": ("as-is", "lower", "upper"),
}
_BOOLS = ("includeViews", "includeKeys", "includeComments", "includePartitions",
          "includeIndexes", "unityCatalog", "external")
_TEXTS = ("catalog", "defaultSchema", "location")
_FILE_ENUMS: dict[str, tuple[str, ...]] = {"nameCase": ("upper", "lower", "as-is"),
                                           "schema": ("when-needed", "always")}
_FILE_TEXTS = ("table", "modeledView", "generatedView", "separator")
_FILE_ILLEGAL = set('\\/:*?"<>|')


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
    return out


def effective_output(raw: dict | None) -> dict:
    """Defaults + lo guardado en el proyecto. Tolerante CLAVE A CLAVE: un valor
    guardado inválido (dato viejo) cae a su default sin descartar los demás."""
    eff = copy.deepcopy(OUTPUT_DEFAULTS)
    for k, v in (raw or {}).items():
        try:
            clean = normalize_output({k: v})
        except ValueError:
            continue
        if "fileNames" in clean:
            eff["fileNames"] = {**eff["fileNames"], **clean.pop("fileNames")}
        eff.update(clean)
    return eff
