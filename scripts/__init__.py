"""One-shot / maintenance scripts.

Not part of the runtime API surface - these modules are intended to be
invoked directly (`python -m scripts.<name>`) from the repo root.

Salida en UTF-8 SIEMPRE: en Windows la consola suele venir en cp1252 y estos
scripts imprimen acentos y simbolos; sin esto un print revienta con
UnicodeEncodeError a media carga. En Linux/macOS es inocuo.
"""

import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # stdout capturado (pytest) o no reconfigurable
        pass
