"""Feature `settings`: configuración de naming (separador/case) por *scope*.

Colección `naming_config` con 1 documento por scope (`column` / `table`). Cada
doc es `{scope, separator, case}` y alimenta el motor de naming (`physicalize`)
para que separador y case sean configurables por tipo de objeto (columna vs
tabla — screens 08a/08b del diseño). Defaults sembrados si el doc no existe:
column = `{separator:'_', case:'upper'}`, table = `{separator:'', case:'upper'}`.

Endpoints:
  GET /api/settings/naming         → ambos scopes (con defaults sembrados).
  PUT /api/settings/naming/{scope} → upsert de {separator, case} de ese scope.

La API pública es `router`.
"""

from .router import router

__all__ = ["router"]
