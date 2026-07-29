# Databricks notebook source
# MAGIC %md
# MAGIC # Carga Erwin (XML) -> Lakebase, desde dentro de Databricks
# MAGIC
# MAGIC Plan B de `plan-implementacion/35-MIGRACION-A-OTRO-DATABRICKS.md`: se usa
# MAGIC cuando el endpoint Lakebase no es alcanzable desde la laptop (el
# MAGIC front-end "service direct" acepta el TCP y descarta el trafico). Desde un
# MAGIC cluster del mismo workspace si se llega.
# MAGIC
# MAGIC **No reimplementa nada**: invoca los MISMOS scripts del repo, con las
# MAGIC mismas reglas de merge, limpieza y prioridad. Solo cambia desde donde se
# MAGIC ejecutan.
# MAGIC
# MAGIC **Todo se parametriza en los widgets de arriba.** Los defaults son los
# MAGIC del entorno actual: al migrar a otro Databricks, esa lista de widgets es
# MAGIC exactamente lo que hay que volver a apuntar.
# MAGIC
# MAGIC ## Prerequisitos
# MAGIC 1. Cluster **classic** (no serverless), DBR con Python 3.10+, single node
# MAGIC    de 16 GB alcanza (el XML grande pide ~0.5 GB de RAM).
# MAGIC 2. Este notebook abierto **desde el repo** (Git folder de `back-dmh-01`),
# MAGIC    para que las rutas relativas al repo funcionen solas.
# MAGIC 3. El cluster con acceso al ADLS (el mismo que ya usas para leer datos).
# MAGIC 4. Tu usuario con rol de Postgres en el proyecto Lakebase.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0. Widgets (correr una vez; quedan en la barra de arriba)

# COMMAND ----------

# `dbutils` lo inyecta el runtime de Databricks; esta linea es solo para que los
# linters de escritorio no lo marquen como indefinido.
dbutils = globals()["dbutils"]  # type: ignore[assignment]

# --- Origen de los XML (ADLS) -------------------------------------------------
dbutils.widgets.text("storage_account", "adlsagentslab01", "1. Storage account")
dbutils.widgets.text("container", "agentsdata", "2. Container")
dbutils.widgets.text("folder_path", "", "3. Carpeta dentro del container")
dbutils.widgets.text("xml1_name", "DDV - CPYBCA.xml", "4. Archivo XML 1")
dbutils.widgets.text("xml2_name", "DDV Modelo de Datos Fisico Otros V0.214.xml", "5. Archivo XML 2")

# --- Destino ------------------------------------------------------------------
dbutils.widgets.text("project", "Modelo de Datos DDV_FISICO", "6. Proyecto destino")
dbutils.widgets.text("lakebase_endpoint",
                     "projects/dmh-proj/branches/production/endpoints/primary",
                     "7. Lakebase endpoint")
dbutils.widgets.text("pghost",
                     "ep-orange-sunset-e1gjz1qx.database.eastus2.azuredatabricks.net",
                     "8. PGHOST")
dbutils.widgets.text("pguser", "carlosperez@bcp.com.pe", "9. PGUSER (Role de Lakebase)")
dbutils.widgets.text("pgschema", "dmh", "10. Schema PG")
dbutils.widgets.text("repo_dir", "", "11. Ruta del repo (vacio = autodetectar)")

# DESTRUCTIVO. Solo para la PRIMERA carga o para rehacerla desde cero.
# Cuando lleguen mas XML para sumar al mismo modelo, esto va en "no".
dbutils.widgets.dropdown("reset_previo", "no", ["no", "si"],
                         "12. RESET previo (BORRA lo ya cargado)")

print("Widgets creados.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Sonda: verifica que DESDE ESTE CLUSTER se llega al 5432
# MAGIC Si no imprime `b'S'`, el cluster tampoco alcanza el endpoint y no hay
# MAGIC nada mas que hacer aca (va el pedido a plataforma).

# COMMAND ----------

import socket
import struct

_s = socket.create_connection((dbutils.widgets.get("pghost"), 5432), 15)
_s.sendall(struct.pack("!ii", 8, 80877103))   # SSLRequest de Postgres
print("respuesta:", _s.recv(1), "  (b'S' = el camino sirve)")
_s.close()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Dependencias
# MAGIC Ruta relativa: este notebook vive en `<repo>/scripts/databricks/`.

# COMMAND ----------

# MAGIC %pip install -r ../../requirements.txt

# COMMAND ----------

dbutils.library.restartPython()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Parametros desde los widgets
# MAGIC (Se leen DESPUES del restart: el restart borra las variables, no los widgets.)

# COMMAND ----------

import os

dbutils = globals()["dbutils"]  # type: ignore[assignment]
W = dbutils.widgets.get

ACCOUNT, CONTAINER = W("storage_account").strip(), W("container").strip()
FOLDER = W("folder_path").strip().strip("/")
PROJECT = W("project").strip()
LAKEBASE_ENDPOINT = W("lakebase_endpoint").strip()
PGHOST, PGUSER = W("pghost").strip(), W("pguser").strip()
PGSCHEMA = W("pgschema").strip() or "dmh"


def adls(nombre: str) -> str:
    ruta = f"{FOLDER}/{nombre}" if FOLDER else nombre
    return f"abfss://{CONTAINER}@{ACCOUNT}.dfs.core.windows.net/{ruta}"


# Slots opcionales: para una carga incremental deja uno vacio y pon el archivo
# nuevo en el otro. Las celdas de un slot vacio no hacen nada.
_n1, _n2 = W("xml1_name").strip(), W("xml2_name").strip()
XML1_SRC = adls(_n1) if _n1 else ""
XML2_SRC = adls(_n2) if _n2 else ""
RESET_PREVIO = W("reset_previo").strip().lower() == "si"

REPO_DIR = W("repo_dir").strip()
if not REPO_DIR:   # autodetectar: el notebook vive en <repo>/scripts/databricks/
    _nb = (dbutils.notebook.entry_point.getDbutils().notebook()
           .getContext().notebookPath().get())
    REPO_DIR = "/Workspace" + _nb.rsplit("/scripts/databricks/", 1)[0]
if not os.path.isfile(os.path.join(REPO_DIR, "requirements.txt")):
    raise ValueError(f"REPO_DIR no parece la raiz del backend: {REPO_DIR}")

print(f"repo     : {REPO_DIR}")
print(f"proyecto : {PROJECT}")
print(f"xml 1    : {XML1_SRC or '(slot vacio: se saltea)'}")
print(f"xml 2    : {XML2_SRC or '(slot vacio: se saltea)'}")
print(f"lakebase : {PGUSER}@{PGHOST} | {LAKEBASE_ENDPOINT} | schema {PGSCHEMA}")
print(f"reset    : {'SI - se borra lo ya cargado' if RESET_PREVIO else 'no'}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Materializar los XML en el driver
# MAGIC `abfss://` no es un filesystem: `open()` no lo lee. Los scripts trabajan
# MAGIC sobre archivo, asi que se baja una vez al disco local del driver (se
# MAGIC saltea si ya esta).

# COMMAND ----------

WORK = "/local_disk0/tmp/erwin" if os.path.isdir("/local_disk0") else "/tmp/erwin"
os.makedirs(WORK, exist_ok=True)


def bajar(uri: str) -> str:
    dst = os.path.join(WORK, uri.rsplit("/", 1)[-1])
    if not os.path.isfile(dst):
        dbutils.fs.cp(uri, "file:" + dst)
    print(f"{dst}: {os.path.getsize(dst) / 1e6:,.0f} MB")
    return dst


XML1 = bajar(XML1_SRC) if XML1_SRC else None
XML2 = bajar(XML2_SRC) if XML2_SRC else None

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Entorno + helper que ejecuta los scripts
# MAGIC El token del propio notebook autentica ante el workspace; el backend acuna
# MAGIC con el el password de Postgres (dura ~1 h y se renueva solo).

# COMMAND ----------

import subprocess
import sys

_ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()

ENV = {
    **os.environ,
    "DB_BACKEND": "lakebase",
    "DATABRICKS_HOST": "https://" + _ctx.browserHostName().get(),
    "DATABRICKS_TOKEN": _ctx.apiToken().get(),
    "LAKEBASE_ENDPOINT": LAKEBASE_ENDPOINT,
    "PGHOST": PGHOST,
    "PGPORT": "5432",
    "PGUSER": PGUSER,
    "PGDATABASE": "databricks_postgres",
    "PGSSLMODE": "require",
    "LAKEBASE_PGSCHEMA": PGSCHEMA,
    "PYTHONUTF8": "1",
    "PYTHONPATH": REPO_DIR,
}


def run(*args: str) -> None:
    """Corre un script del repo y streamea su salida al notebook."""
    cmd = [sys.executable, *args]
    print(">", " ".join(cmd), "\n")
    p = subprocess.Popen(cmd, cwd=REPO_DIR, env=ENV, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", bufsize=1)
    for line in p.stdout:
        print(line, end="")
    p.wait()
    if p.returncode != 0:
        raise RuntimeError(f"El script termino con codigo {p.returncode}")


print("listo | python:", sys.version.split()[0])

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. RESET previo (opcional, DESTRUCTIVO)
# MAGIC
# MAGIC Deja la base lista para una carga limpia: vacia **modelo**
# MAGIC (proyectos, folders, canvases, schemas, tablas, columnas, relaciones,
# MAGIC vistas), **estandares** (dominios, glosario, definiciones UDP, versiones)
# MAGIC y **governance** (changesets). PRESERVA usuarios, roles, `naming_config`,
# MAGIC `column_catalog` y el audit log.
# MAGIC
# MAGIC **Cuando SI**: primera carga sobre una base que quedo con restos de
# MAGIC pruebas, o rehacer todo desde cero.
# MAGIC **Cuando NO**: cargas incrementales (llego un XML nuevo del mismo
# MAGIC modelo). Ahi borrarias justamente lo que ya cargaste.
# MAGIC
# MAGIC Dos candados: la celda de abajo es solo dry-run, y el `--apply` no corre
# MAGIC salvo que el widget `reset_previo` diga `si`.

# COMMAND ----------

run("-m", "scripts.reset_for_migration")          # dry-run: solo lista que borraria

# COMMAND ----------

if RESET_PREVIO:
    run("-m", "scripts.reset_for_migration", "--apply")
else:
    print("reset_previo = no -> no se borro nada (revisa el dry-run de arriba).")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Usuario para entrar a la web
# MAGIC Crea `admin` / `admin` y los 4 roles con su matriz de permisos default.
# MAGIC Los usuarios reales se crean despues desde Admin (la plataforma tiene su
# MAGIC propio padron, no hereda los del workspace). Cambia esa clave al entrar.

# COMMAND ----------

run("scripts/create_admin.py")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. XML 1 (CPYBCA): calidad -> cruce vs BD -> dry-run -> apply
# MAGIC El orden importa: CPYBCA primero, Otros despues (el desempate por uso
# MAGIC actualiza sobre lo ya cargado).

# COMMAND ----------

if XML1:
    run("-m", "scripts.erwin_migration.quality", XML1)

# COMMAND ----------

if XML1:
    run("-m", "scripts.erwin_migration.crosscheck", XML1)

# COMMAND ----------

if XML1:
    run("-m", "scripts.erwin_migration.migrate", XML1, "--project", PROJECT)

# COMMAND ----------

if XML1:
    run("-m", "scripts.erwin_migration.migrate", XML1, "--project", PROJECT, "--apply")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9. XML 2 (Otros V0.214): mismo flujo, MISMO proyecto
# MAGIC Aca actua el merge multi-archivo: adopcion por clave natural, conflicto
# MAGIC resuelto por score de uso, dedup de FKs, fusion de folders y canvases
# MAGIC homonimos, particiones por orden fisico, vistas sin fuente descartadas.

# COMMAND ----------

if XML2:
    run("-m", "scripts.erwin_migration.quality", XML2)

# COMMAND ----------

if XML2:
    run("-m", "scripts.erwin_migration.crosscheck", XML2)

# COMMAND ----------

if XML2:
    run("-m", "scripts.erwin_migration.migrate", XML2, "--project", PROJECT)

# COMMAND ----------

if XML2:
    run("-m", "scripts.erwin_migration.migrate", XML2, "--project", PROJECT, "--apply")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 10. Validacion de la data cargada
# MAGIC Esperado: 0 fixables. Los informativos son politicas conocidas (doc 34).

# COMMAND ----------

run("-m", "scripts.audit_data_consistency")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 11. Reglas base de DDL Export
# MAGIC Glosario, dominios padre y definiciones UDP ya entraron con `migrate`.
# MAGIC Esto siembra el ruleset de exportacion: 8 reglas + los lookups
# MAGIC `vacuum_map` / `dac_map`, como una version de Data Standards.

# COMMAND ----------

run("-m", "scripts.seed_ddl_export_rules")            # dry-run

# COMMAND ----------

run("-m", "scripts.seed_ddl_export_rules", "--apply")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 12. Layout de canvases (opcional: necesita Node)
# MAGIC `arrange_all` corre elkjs. Si el cluster no trae Node se saltea: la
# MAGIC migracion ya deja layout en grilla y la web tiene el boton "Autoarrange"
# MAGIC por canvas.

# COMMAND ----------

if subprocess.run(["which", "node"], capture_output=True).returncode == 0:
    npm_dir = os.path.join(WORK, "elk")
    os.makedirs(npm_dir, exist_ok=True)
    subprocess.run(["npm", "install", "elkjs"], cwd=npm_dir, check=True)
    ENV["ELKJS_PATH"] = os.path.join(npm_dir, "node_modules", "elkjs", "lib", "elk.bundled.js")
    run("scripts/arrange_all.py", "--project", PROJECT)
else:
    print("Node no esta disponible en el cluster: saltear el auto-arrange.")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 13. Cierre
# MAGIC
# MAGIC En la web: crear una version vacia **v1 Base** (submit + approve, 0
# MAGIC cambios). Recien despues tiene sentido `reset_to_base_version --apply`,
# MAGIC que ademas deja el baseline de Data Standards y el permiso de rollback.
# MAGIC
# MAGIC Conteos esperados con los 2 archivos: 1 proyecto, 2108 tablas, 96184
# MAGIC columnas, 1630 relaciones, 1932 vistas, 275 canvases, 388 schemas,
# MAGIC 48 folders.

# COMMAND ----------

# run("-m", "scripts.reset_to_base_version")             # dry-run
# run("-m", "scripts.reset_to_base_version", "--apply")  # DESTRUCTIVO: leer el dry-run antes
