"""Versionado: overlay de changesets sobre lo publicado (puro). Ver `overlay.py`."""
from .overlay import overlay, plain, reserved_key, summarize_diff

__all__ = ["overlay", "plain", "reserved_key", "summarize_diff"]
