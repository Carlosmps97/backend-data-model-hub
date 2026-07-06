"""Primitivas de seguridad de la auth propia: hash de contraseñas (bcrypt) y
token de sesión firmado (JWT HS256).

Puro y sin estado (salvo `settings.SECRET_KEY`): testeable sin DB. La feature
`auth` orquesta estas funciones con el repositorio de usuarios.
"""
from __future__ import annotations

import datetime as _dt

import bcrypt
import jwt

from app.core.config import settings

# bcrypt trunca a 72 bytes; passwords más largas se recortan de forma
# determinista (mismo texto ⇒ mismo hash/verify). Documentado, no silencioso.
_BCRYPT_MAX_BYTES = 72
_ALG = "HS256"


def _clip(password: str) -> bytes:
    return password.encode("utf-8")[:_BCRYPT_MAX_BYTES]


def hash_password(password: str) -> str:
    """Hash bcrypt (con salt aleatorio embebido). Devuelve el string a persistir."""
    return bcrypt.hashpw(_clip(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, hashed: str) -> bool:
    """Compara en tiempo constante (bcrypt.checkpw). False si el hash es inválido
    (p.ej. usuario sin passwordHash) en vez de reventar."""
    if not hashed:
        return False
    try:
        return bcrypt.checkpw(_clip(password), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(subject: str, *, extra: dict | None = None,
                        ttl_min: int | None = None, now: _dt.datetime | None = None) -> str:
    """Token de sesión firmado (HS256). `subject` = username. `extra` agrega
    claims (p.ej. role). `now` inyectable para tests deterministas."""
    now = now or _dt.datetime.now(_dt.timezone.utc)
    ttl = ttl_min if ttl_min is not None else settings.ACCESS_TOKEN_TTL_MIN
    payload = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + _dt.timedelta(minutes=ttl)).timestamp()),
        **(extra or {}),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=_ALG)


def decode_access_token(token: str) -> dict | None:
    """Valida firma + expiración. Devuelve el payload o None si es inválido/
    expirado (nunca levanta — el caller decide 401)."""
    try:
        return jwt.decode(token, settings.SECRET_KEY, algorithms=[_ALG])
    except jwt.PyJWTError:
        return None
