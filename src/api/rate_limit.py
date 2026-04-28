"""Rate limiting y retry con backoff para llamadas al LLM.

Resuelve dos problemas observados en producción:

1. **429 rate_limit_exceeded** de Azure OpenAI Foundry. La SDK del OpenAI
   ya hace 2 reintentos con backoff corto (~5 s). Cuando varias tablas
   corren en paralelo eso no alcanza y la excepción propaga al pipeline,
   que pierde la tabla.

2. **Saturación silenciosa**: el pipeline pisa el quota porque emite
   ráfagas concentradas (3 pipelines × ~5 LLM calls = 15 RPM por sub-segundo).

La solución combinada:

- `LLMRateLimiter`: token bucket simple por minuto. Espera antes de
  permitir una nueva llamada cuando se está cerca del cap. Es un
  compañero del semáforo del pipeline.
- `retry_on_rate_limit`: decorator/wrapper async que detecta 429 (por
  string en el mensaje, ya que el FoundryChatClient envuelve el error
  en una `Exception` cualquiera) y reintenta con backoff exponencial
  + jitter.

Ambos primitivos son thread-safe vía `asyncio.Lock`.
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from typing import Any, Awaitable, Callable, TypeVar

from src.logger import get_logger

log = get_logger(__name__)

T = TypeVar("T")


# ─── Rate limiter por minuto (token bucket simple) ─────────────────────


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        v = int(raw)
    except ValueError:
        return default
    return max(minimum, v)


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        v = float(raw)
    except ValueError:
        return default
    return max(minimum, v)


class LLMRateLimiter:
    """Token bucket simple basado en ventana deslizante de 60 s.

    Cada llamada `await acquire()` reserva un token. Si la ventana de los
    últimos 60 s ya tiene `cap` reservas, el caller espera hasta que
    expire la primera reserva.

    Diseño:
    - Uso de `asyncio.Lock` para coordinar entre tasks del mismo loop.
    - No usamos primitives entre procesos: este módulo está pensado para
      una sola instancia del backend (single-process FastAPI dev). En
      multiprocess habría que usar Redis o similar.
    - `cap` se lee de la env var `LLM_RPM_CAP` y por defecto vale 30, que
      es conservador para el quota típico de Azure OpenAI Foundry GPT-4o.
    """

    def __init__(self, cap_per_minute: int) -> None:
        self.cap = max(1, cap_per_minute)
        self._reservations: list[float] = []
        self._lock = asyncio.Lock()

    async def acquire(self, label: str = "") -> None:
        """Bloquea hasta que haya cupo en la ventana de 60 s."""
        while True:
            async with self._lock:
                now = time.monotonic()
                # Soltar reservas más viejas que 60 s.
                cutoff = now - 60.0
                self._reservations = [t for t in self._reservations if t > cutoff]
                if len(self._reservations) < self.cap:
                    self._reservations.append(now)
                    return
                # Cuánto falta para que expire la más vieja.
                wait_for = (self._reservations[0] + 60.0) - now
            wait_for = max(0.05, wait_for + random.uniform(0.0, 0.25))
            log.info(
                "rate limit reached, waiting",
                extra={"cap_per_minute": self.cap, "wait_s": round(wait_for, 2),
                       "label": label or "anon"},
            )
            await asyncio.sleep(wait_for)


# Singleton de proceso. Se inyecta directamente en los endpoints que lo
# necesiten. La env var permite tunear sin tocar código.
LLM_RPM_CAP = _env_int("LLM_RPM_CAP", default=30, minimum=1)
llm_rate_limiter = LLMRateLimiter(cap_per_minute=LLM_RPM_CAP)


# ─── Retry con backoff para 429 ────────────────────────────────────────


# Ventanas de espera (segundos) entre reintentos cuando detectamos 429.
# Total con default = ~80 s en el peor caso para 4 reintentos. Más que
# suficiente para que Azure recupere el bucket per-minute.
_RETRY_BACKOFFS = [5.0, 15.0, 30.0, 45.0]
_MAX_RETRIES = _env_int("LLM_MAX_RETRIES", default=len(_RETRY_BACKOFFS), minimum=0)


def _is_rate_limit_error(exc: BaseException) -> bool:
    """Detecta si una excepción corresponde a un 429.

    El FoundryChatClient envuelve el error original en una `Exception`
    que serializa el mensaje de Azure. Usamos string-matching defensivo
    porque el tipo concreto cambia entre versiones de la SDK.
    """
    msg = str(exc)
    if not msg:
        return False
    msg_lower = msg.lower()
    return (
        "429" in msg
        or "rate_limit_exceeded" in msg_lower
        or "rate limit" in msg_lower
        or "too many requests" in msg_lower
    )


async def call_with_retry(
    fn: Callable[[], Awaitable[T]],
    *,
    label: str = "",
    max_retries: int = _MAX_RETRIES,
) -> T:
    """Ejecuta `fn()` con retry exponencial cuando detecta 429.

    Se reintenta hasta `max_retries` veces (default = 4). Otros errores
    se propagan inmediatamente. Cada reintento espera un valor con
    jitter aleatorio para des-sincronizar tareas paralelas.
    """
    attempt = 0
    last_exc: BaseException | None = None

    while True:
        try:
            return await fn()
        except Exception as e:
            if not _is_rate_limit_error(e):
                raise
            last_exc = e
            attempt += 1
            if attempt > max_retries:
                log.error(
                    "rate limit retries exhausted",
                    extra={"label": label or "anon", "attempts": attempt,
                           "error": str(e)[:200]},
                )
                raise
            # Backoff escalado por intento (clamp a la última ventana).
            base = _RETRY_BACKOFFS[min(attempt - 1, len(_RETRY_BACKOFFS) - 1)]
            delay = base + random.uniform(0.0, base * 0.4)
            log.warning(
                "rate limit hit, retrying",
                extra={
                    "label": label or "anon",
                    "attempt": attempt,
                    "max_retries": max_retries,
                    "delay_s": round(delay, 2),
                },
            )
            await asyncio.sleep(delay)

    # Por completitud: nunca debería llegar acá.
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("call_with_retry exited without result")


__all__ = [
    "LLMRateLimiter",
    "LLM_RPM_CAP",
    "llm_rate_limiter",
    "call_with_retry",
]
