#!/usr/bin/env bash
# Pre-arma un mirror LOCAL del provider Terraform de Databricks para que
# `databricks bundle deploy` no falle verificando la firma del provider.
#
# Contexto: el CLI de Databricks usa OpenTofu por debajo y fija el provider a
# `registry.opentofu.org/databricks/databricks`. En el runner, `tofu init`
# verifica la firma contra ese registry y falla ("authentication signature from
# unknown issuer" — el provider es experimental / no testeado en OpenTofu).
# En cambio `tofu providers mirror` baja el provider OMITIENDO la firma
# ("signing skipped") y `tofu init` desde un `filesystem_mirror` tampoco la
# verifica. Así que se pre-baja a un mirror local y el CLI instala desde ahí.
#
# Requisitos en el runner: databricks CLI, tofu (OpenTofu), jq.
# Uso: BUNDLE_TARGET=prod bash scripts/ci/tf-provider-mirror.sh
set -euo pipefail

TARGET="${BUNDLE_TARGET:-prod}"
TMP="${RUNNER_TEMP:-/tmp}"
MIRROR="$TMP/tf-mirror"
RC="$TMP/tf-cli.tfrc"
SRC="$TMP/tf-provider-src"

# 1. Versión EXACTA del provider que quiere el CLI: la escribe en el tf.json
#    generado. `validate` no instala providers, así que no falla acá. Si no se
#    puede leer, se baja la última (= la que el CLI pide hoy: v1.121.0).
databricks bundle validate --target "$TARGET" >/dev/null 2>&1 || true
TF_JSON="$(find .databricks -name 'bundle.tf.json' 2>/dev/null | head -n1 || true)"
[ -z "${TF_JSON:-}" ] && TF_JSON="$(find .databricks -name '*.tf.json' 2>/dev/null | head -n1 || true)"
if [ -z "${TF_JSON:-}" ]; then
  # Fallback: un deploy que falla en el init igual deja escrito el tf.json.
  databricks bundle deploy --target "$TARGET" >/dev/null 2>&1 || true
  TF_JSON="$(find .databricks -name 'bundle.tf.json' 2>/dev/null | head -n1 || true)"
  [ -z "${TF_JSON:-}" ] && TF_JSON="$(find .databricks -name '*.tf.json' 2>/dev/null | head -n1 || true)"
fi
VER="$(jq -r '.terraform.required_providers.databricks.version // empty' "${TF_JSON:-/dev/null}" 2>/dev/null || true)"
VER="$(printf '%s' "${VER:-}" | grep -oE '[0-9]+(\.[0-9]+)+' | head -n1 || true)"
echo "Provider databricks/databricks = ${VER:-(última disponible)}"

# 2. Mirror del provider con OpenTofu: baja bajo el host registry.opentofu.org
#    (el MISMO que el CLI pedirá) OMITIENDO la verificación de firma.
rm -rf "$SRC" "$MIRROR"; mkdir -p "$SRC" "$MIRROR"
if [ -n "${VER:-}" ]; then VLINE="      version = \"$VER\""; else VLINE=""; fi
cat > "$SRC/providers.tf" <<TF
terraform {
  required_providers {
    databricks = {
      source  = "databricks/databricks"
$VLINE
    }
  }
}
TF
( cd "$SRC" && tofu providers mirror -platform=linux_amd64 "$MIRROR" )

# 3. Config: instalar TODOS los providers desde el mirror local (sin firma).
cat > "$RC" <<RC
provider_installation {
  filesystem_mirror {
    path    = "$MIRROR"
    include = ["*/*/*"]
  }
  direct {
    exclude = ["*/*/*"]
  }
}
RC

# 4. Exportar la config para los pasos siguientes (validate/deploy del workflow).
{
  echo "DATABRICKS_TF_CLI_CONFIG_FILE=$RC"
  echo "TF_CLI_CONFIG_FILE=$RC"
} >> "${GITHUB_ENV:-/dev/null}"
echo "OK: mirror en $MIRROR · config en $RC"
