"""Logging estructurado del backend de plataforma.

Soporta dos formatos controlados por la variable de entorno `LOG_FORMAT`:

- `pretty` (default): salida legible por humanos, ideal para desarrollo.
- `json`: cada log es un objeto JSON con timestamp ISO-8601, level, logger,
  message y cualquier campo extra pasado como `extra={"key": value}`.

Uso recomendado:
    from app.core.logging import get_logger
    log = get_logger(__name__)
    log.info("pipeline started", extra={"conversation_id": cid, "turn": n})

Notas:
- `LOG_LEVEL` controla el nivel mínimo (default INFO).
- Se configura una sola vez por proceso (`_configured` guard).
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any

_DEFAULT_FORMAT = "pretty"
_VALID_FORMATS = {"pretty", "json"}

# Atributos estándar del LogRecord que NO consideramos "extras".
_STANDARD_LOG_RECORD_ATTRS: frozenset[str] = frozenset(
    {
        "name", "msg", "args", "levelname", "levelno", "pathname",
        "filename", "module", "exc_info", "exc_text", "stack_info",
        "lineno", "funcName", "created", "msecs", "relativeCreated",
        "thread", "threadName", "processName", "process", "taskName",
        "message", "asctime",
    }
)

_configured = False


def _iso_timestamp(record: logging.LogRecord) -> str:
    """Convierte el timestamp del record a ISO-8601 UTC con milisegundos."""
    dt = datetime.fromtimestamp(record.created, tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(record.msecs):03d}Z"


def _record_extras(record: logging.LogRecord) -> dict[str, Any]:
    """Extrae los campos `extra` adicionales agregados al record."""
    extras: dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key in _STANDARD_LOG_RECORD_ATTRS or key.startswith("_"):
            continue
        if value is None:
            continue
        try:
            json.dumps(value)
            extras[key] = value
        except (TypeError, ValueError):
            extras[key] = repr(value)
    return extras


class _JsonFormatter(logging.Formatter):
    """Formatter JSON: un objeto por línea."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": _iso_timestamp(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(_record_extras(record))
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class _PrettyFormatter(logging.Formatter):
    """Formatter legible para desarrollo. Anexa extras en formato `key=value`."""

    def __init__(self) -> None:
        super().__init__(
            fmt="%(asctime)s %(levelname)-5s %(name)s - %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        extras = _record_extras(record)
        if extras:
            extras_str = " ".join(f"{k}={v}" for k, v in extras.items())
            base = f"{base} | {extras_str}"
        return base


def _build_handler() -> logging.Handler:
    """Construye el handler raíz según `settings.LOG_FORMAT`."""
    from app.core.config import settings

    fmt = settings.LOG_FORMAT
    if fmt not in _VALID_FORMATS:
        fmt = _DEFAULT_FORMAT
    handler = logging.StreamHandler(stream=sys.stdout)
    handler.setFormatter(_JsonFormatter() if fmt == "json" else _PrettyFormatter())
    return handler


def configure_logging() -> None:
    """Configura el logger raíz una única vez por proceso."""
    global _configured
    if _configured:
        return

    from app.core.config import settings

    level = getattr(logging, settings.LOG_LEVEL, logging.INFO)

    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)

    root.addHandler(_build_handler())
    root.setLevel(level)

    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Atajo para obtener un logger configurado."""
    if not _configured:
        configure_logging()
    return logging.getLogger(name)
