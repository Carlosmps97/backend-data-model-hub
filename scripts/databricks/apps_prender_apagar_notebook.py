# Databricks notebook source
# MAGIC %md
# MAGIC # Prender / apagar las apps del Data Model Hub
# MAGIC
# MAGIC Pensado para un **Job programado** (cada hora o solo dos veces al día):
# MAGIC dentro del horario prende las apps que estén apagadas y fuera de él apaga
# MAGIC las que estén prendidas. Solo llama a la API cuando hace falta, espera a
# MAGIC que la operación termine y, si algo falla, muestra el **motivo que
# MAGIC devuelve Databricks** y deja el Job en rojo. (La versión anterior no
# MAGIC revisaba la respuesta del `POST`: un arranque fallido pasaba en silencio.)
# MAGIC
# MAGIC Prender o apagar **no sube archivos ni crea deployments**: al prender,
# MAGIC Databricks vuelve a levantar el último deployment de la app (instala
# MAGIC paquetes y, en el front, corre el build). El código solo cambia con el
# MAGIC deploy de GitHub Actions.
# MAGIC
# MAGIC ## Identidad
# MAGIC Por defecto actúa con la identidad que corre el notebook (el dueño del Job
# MAGIC o su *Run as*), que necesita **CAN_MANAGE** sobre las apps. Si se llenan
# MAGIC `secret_scope` y `secret_key`, usa en cambio el token guardado en ese
# MAGIC secreto.

# COMMAND ----------

# `dbutils` lo inyecta el runtime de Databricks; esta línea es solo para que los
# linters de escritorio no lo marquen como indefinido.
dbutils = globals()["dbutils"]  # type: ignore[assignment]

dbutils.widgets.text("apps", "bknd-data-model-hub,frnt-data-model-hub",
                     "1. Apps (en orden de arranque)")
dbutils.widgets.dropdown("accion", "auto", ["auto", "start", "stop"],
                         "2. Acción (auto = según la hora)")
dbutils.widgets.text("hora_inicio", "9", "3. Hora de prendido (0-23)")
dbutils.widgets.text("hora_fin", "23", "4. Hora de apagado (0-23)")
dbutils.widgets.text("zona_horaria", "America/Lima", "5. Zona horaria")
dbutils.widgets.text("secret_scope", "", "6. (opcional) Scope del token")
dbutils.widgets.text("secret_key", "", "7. (opcional) Key del token")
dbutils.widgets.text("espera_min", "20", "8. Minutos máximos de espera por app")

# COMMAND ----------

import time
from datetime import datetime
from zoneinfo import ZoneInfo

from databricks.sdk import WorkspaceClient

APPS = [a.strip() for a in dbutils.widgets.get("apps").split(",") if a.strip()]
ACCION = dbutils.widgets.get("accion")
SCOPE = dbutils.widgets.get("secret_scope").strip()
KEY = dbutils.widgets.get("secret_key").strip()
ESPERA_S = int(dbutils.widgets.get("espera_min")) * 60

if ACCION == "auto":
    hora = datetime.now(ZoneInfo(dbutils.widgets.get("zona_horaria"))).hour
    dentro = int(dbutils.widgets.get("hora_inicio")) <= hora < int(dbutils.widgets.get("hora_fin"))
    ACCION = "start" if dentro else "stop"
    print(f"Hora {hora}:00 -> {ACCION}")

# Host y credencial del propio notebook; con scope/key, el token del secreto.
w = WorkspaceClient()
if SCOPE and KEY:
    w = WorkspaceClient(host=w.config.host, token=dbutils.secrets.get(SCOPE, KEY))

# Estados en tránsito: se espera a que terminen antes de decidir.
TRANSITORIOS = {"STARTING", "STOPPING", "UPDATING", "DELETING"}


def api(method, path):
    """Apps API; un error trae el mensaje de Databricks (motivo real)."""
    return w.api_client.do(method, f"/api/2.0/apps/{path}") or {}


def estado(app):
    """(compute, app, mensaje) — p. ej. ('ACTIVE', 'RUNNING', '')."""
    a = api("GET", app)
    compute = a.get("compute_status") or {}
    runtime = a.get("app_status") or {}
    mensaje = runtime.get("message") or compute.get("message") or ""
    return compute.get("state", "?"), runtime.get("state", "?"), mensaje


def ultimo_update(app):
    """Estado del último «App update» (cambio de configuración de la app)."""
    try:
        status = api("GET", f"{app}/update").get("status") or {}
        return f"{status.get('state', '?')} {status.get('message', '')}".strip()
    except Exception:
        return "sin datos"


def esperar(app, listo, limite, fallas=()):
    """Consulta cada 15 s hasta que listo(compute, app) sea verdadero."""
    while True:
        compute, runtime, mensaje = estado(app)
        if listo(compute, runtime):
            return compute, runtime
        if compute in fallas or runtime in fallas:
            raise RuntimeError(f"{compute} / {runtime}: {mensaje}")
        if time.time() > limite:
            raise TimeoutError(f"sigue en {compute} / {runtime} tras la espera: {mensaje}")
        time.sleep(15)


def prender(app, limite):
    compute, runtime, _ = estado(app)
    if compute in TRANSITORIOS:
        compute, runtime = esperar(app, lambda c, r: c not in TRANSITORIOS, limite)
    if compute == "ACTIVE" and runtime == "RUNNING":
        return "ya estaba prendida"
    if compute in ("STOPPED", "ERROR"):
        api("POST", f"{app}/start")
    # Cómputo arriba: falta que el deployment quede RUNNING (o que falle).
    esperar(app, lambda c, r: c == "ACTIVE" and r == "RUNNING", limite,
            fallas=("ERROR", "CRASHED"))
    return "prendida"


def apagar(app, limite):
    compute, _, _ = estado(app)
    if compute in TRANSITORIOS:
        compute, _ = esperar(app, lambda c, r: c not in TRANSITORIOS, limite)
    if compute == "STOPPED":
        return "ya estaba apagada"
    api("POST", f"{app}/stop")
    esperar(app, lambda c, r: c == "STOPPED", limite)
    return "apagada"


# El backend arranca primero (el front lo consume) y se apaga al final.
orden = APPS if ACCION == "start" else list(reversed(APPS))
operacion = prender if ACCION == "start" else apagar
fallidas = []
for app in orden:
    try:
        resultado = operacion(app, time.time() + ESPERA_S)
        compute, runtime, _ = estado(app)
        print(f"OK    {app}: {resultado} ({compute} / {runtime})")
    except Exception as e:
        fallidas.append(app)
        print(f"ERROR {app}: {e}")
        print(f"      Último «App update»: {ultimo_update(app)}")

if fallidas:
    raise Exception(f"No se pudo completar '{ACCION}' en: {', '.join(fallidas)} (motivo arriba).")
