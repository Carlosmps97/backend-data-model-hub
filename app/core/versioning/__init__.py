"""Versionado: overlay de changesets sobre lo publicado (puro). Ver `overlay.py`."""
from .overlay import overlay, summarize_diff

__all__ = ["overlay", "summarize_diff"]
