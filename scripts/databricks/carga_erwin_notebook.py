# Databricks notebook source
# MAGIC %md
# MAGIC # Carga Erwin (XML) -> Lakebase, desde dentro de Databricks
# MAGIC
# MAGIC Ejecuta el ORQUESTADOR `scripts/run_migration.py` (doc 54) sobre los XML
# MAGIC bajados de ADLS. **No reimplementa nada**: el orquestador invoca los
# MAGIC MISMOS scripts del repo (quality, reset, create_admin, migrate, audit,
# MAGIC seed de data functions, arrange, mark_base_version) con las mismas
# MAGIC reglas de merge, `_DUPn` y glosario cruzado.
# MAGIC
# MAGIC Dos modos (widget `modo`):
# MAGIC - **one-shot** (DESTRUCTIVO): baja TODOS los `.xml` de la carpeta raiz
# MAGIC   (recursivo, espejando subcarpetas) y deja la plataforma como un primer
# MAGIC   deployment — borra TODO el schema, recrea los 4 roles de caja +
# MAGIC   `admin`/`admin`, migra archivo por archivo (SECUENCIAL a proposito),
# MAGIC   siembra las data functions, hace el layout y marca la version base v1
# MAGIC   de cada proyecto. El proyecto destino de cada archivo lo decide el
# MAGIC   MANIFIESTO `projects.json` en la carpeta raiz (doc 75: p. ej. todos los
# MAGIC   DDV → «Modelo DDV»); sin entrada en el manifiesto, el nombre del
# MAGIC   archivo (un XML = un proyecto).
# MAGIC - **append** (no destructivo): baja UN archivo y lo SUMA a la BD viva
# MAGIC   (sin reset, sin admin, sin seeds, sin marcador).
# MAGIC
# MAGIC El gate de calidad corre ANTES de borrar nada: si falla, la BD queda
# MAGIC intacta (salvo `force = si`, que continua omitiendo los objetos con ERROR).
# MAGIC
# MAGIC ## Prerequisitos
# MAGIC 1. Cluster **classic** (no serverless), DBR con Python 3.10+, single node
# MAGIC    de 16 GB alcanza (el XML grande pide ~0.5 GB de RAM).
# MAGIC 2. Este notebook abierto **desde el repo** (Git folder de `back-dmh-01`),
# MAGIC    para que las rutas relativas al repo funcionen solas.
# MAGIC 3. Los XML en ADLS (`abfss://`) y el cluster con access mode
# MAGIC    **Dedicated** (antes "Single user"): la copia al disco del driver lo
# MAGIC    requiere. OJO: la **Policy** ("Unrestricted") NO es el access mode; ese
# MAGIC    vive en Compute > Edit > Advanced > Access mode (en "Standard/Shared"
# MAGIC    la copia falla con "on Shared cluster").
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
dbutils.widgets.text("folder_path", "",
                     "3. Carpeta RAIZ de los .xml (lectura recursiva)")

# --- Modo de carga ------------------------------------------------------------
dbutils.widgets.dropdown("modo", "one-shot", ["one-shot", "append"],
                         "4. Modo (one-shot = BORRA TODO y carga)")
dbutils.widgets.text("archivo_append", "",
                     "5. (append) Ruta del .xml dentro del container")
dbutils.widgets.text("append_project", "",
                     "6. (append) Proyecto destino (vacio = nombre del archivo)")
dbutils.widgets.text("base_title", "Base - Migración Erwin (XML)",
                     "7. (one-shot) Titulo de la version base v1")
dbutils.widgets.dropdown("force", "no", ["no", "si"],
                         "8. force (seguir aunque el gate tenga ERRORs)")
dbutils.widgets.dropdown("confirmar_wipe", "no", ["no", "si"],
                         "9. CONFIRMAR one-shot (BORRA TODA LA BD)")

# --- Destino ------------------------------------------------------------------
dbutils.widgets.text("lakebase_endpoint",
                     "projects/dmh-proj/branches/production/endpoints/primary",
                     "10. Lakebase endpoint")
dbutils.widgets.text("pghost",
                     "ep-orange-sunset-e1gjz1qx.database.eastus2.azuredatabricks.net",
                     "11. PGHOST")
dbutils.widgets.text("pguser", "carlosperez@bcp.com.pe", "12. PGUSER (Role de Lakebase)")
dbutils.widgets.text("pgschema", "dmh", "13. Schema PG")
dbutils.widgets.text("repo_dir", "", "14. Ruta del repo (vacio = autodetectar)")

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
MODO = W("modo").strip()
ARCHIVO_APPEND = W("archivo_append").strip().strip("/")
APPEND_PROJECT = W("append_project").strip()
BASE_TITLE = W("base_title").strip() or "Base - Migración Erwin (XML)"
FORCE = W("force").strip().lower() == "si"
CONFIRMAR_WIPE = W("confirmar_wipe").strip().lower() == "si"
LAKEBASE_ENDPOINT = W("lakebase_endpoint").strip()
PGHOST, PGUSER = W("pghost").strip(), W("pguser").strip()
PGSCHEMA = W("pgschema").strip() or "dmh"

if MODO == "one-shot" and not FOLDER:
    raise ValueError("modo one-shot necesita `folder_path` (carpeta raiz de los XML).")
if MODO == "append" and not ARCHIVO_APPEND:
    raise ValueError("modo append necesita `archivo_append` (ruta del .xml).")


def adls(ruta: str) -> str:
    return f"abfss://{CONTAINER}@{ACCOUNT}.dfs.core.windows.net/{ruta}"


REPO_DIR = W("repo_dir").strip()
if not REPO_DIR:
    # El notebook vive en <repo>/scripts/databricks/ y su CWD es esa carpeta.
    REPO_DIR = os.path.abspath(os.path.join(os.getcwd(), "..", ".."))
if not os.path.isfile(os.path.join(REPO_DIR, "requirements.txt")):
    raise ValueError(f"REPO_DIR no parece la raiz del backend: {REPO_DIR}")

print(f"repo     : {REPO_DIR}")
print(f"modo     : {MODO}{' (FORCE)' if FORCE else ''}")
if MODO == "one-shot":
    print(f"carpeta  : {adls(FOLDER)}  (recursivo)")
    print(f"v1 title : {BASE_TITLE}")
    print(f"wipe     : {'CONFIRMADO - se borra TODO' if CONFIRMAR_WIPE else 'no confirmado (solo dry-run)'}")
else:
    print(f"archivo  : {adls(ARCHIVO_APPEND)}")
    print(f"proyecto : {APPEND_PROJECT or '(auto: nombre del archivo)'}")
print(f"lakebase : {PGUSER}@{PGHOST} | {LAKEBASE_ENDPOINT} | schema {PGSCHEMA}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Materializar los XML en el driver
# MAGIC `abfss://` no es un filesystem: `open()` no lo lee. Se baja al disco local
# MAGIC del driver — en one-shot, TODA la carpeta recursivamente, espejando las
# MAGIC subcarpetas (asi dos archivos homonimos de distintas subcarpetas no se
# MAGIC pisan) mas el manifiesto `projects.json` de la raiz, si existe. Requiere
# MAGIC access mode **Dedicated**.

# COMMAND ----------

WORK = "/local_disk0/tmp/erwin" if os.path.isdir("/local_disk0") else "/tmp/erwin"
os.makedirs(WORK, exist_ok=True)


def bajar(uri: str, dst: str) -> str:
    if not os.path.isfile(dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        try:
            dbutils.fs.cp(uri, "file:" + dst)
        except Exception as exc:
            if "Shared cluster" in str(exc) or "non /Workspace" in str(exc):
                raise RuntimeError(
                    "El cluster corre con ACCESS MODE 'Standard/Shared' y ahi "
                    "Databricks prohibe escribir al disco del driver. Cambia a "
                    "Compute > Edit > Advanced > Access mode > 'Dedicated' "
                    "(la Policy 'Unrestricted' es otra perilla) y reintenta."
                ) from exc
            raise
    print(f"{dst}: {os.path.getsize(dst) / 1e6:,.0f} MB")
    return dst


def walk_xmls(uri: str) -> list:
    """Todos los .xml bajo `uri` (recursivo), via dbutils.fs.ls."""
    out = []
    for f in dbutils.fs.ls(uri):
        if f.isDir():
            out += walk_xmls(f.path)
        elif f.name.lower().endswith(".xml"):
            out.append(f)
    return out


if MODO == "one-shot":
    LOCAL_ROOT = os.path.join(WORK, "carpeta")
    base_uri = adls(FOLDER).rstrip("/") + "/"
    files = walk_xmls(base_uri)
    if not files:
        raise ValueError(f"No hay .xml bajo {base_uri} (busqueda recursiva).")
    for f in files:
        rel = f.path[len(base_uri):] if f.path.startswith(base_uri) else f.name
        bajar(f.path, os.path.join(LOCAL_ROOT, *rel.split("/")))
    print(f"\n{len(files)} XML materializados bajo {LOCAL_ROOT}")
    # Doc 75: el manifiesto de proyectos vive en la raiz de la carpeta.
    manifest = [f for f in dbutils.fs.ls(base_uri) if f.name == "projects.json"]
    if manifest:
        bajar(manifest[0].path, os.path.join(LOCAL_ROOT, "projects.json"))
    else:
        print("sin projects.json en la raiz: cada XML sera un proyecto con el nombre del archivo")
    TARGET = LOCAL_ROOT
else:
    TARGET = bajar(adls(ARCHIVO_APPEND),
                   os.path.join(WORK, "append", ARCHIVO_APPEND.rsplit("/", 1)[-1]))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Entorno + helper que ejecuta los scripts
# MAGIC El token del propio notebook autentica ante el workspace; el backend acuna
# MAGIC con el el password de Postgres (dura ~1 h y se renueva solo).

# COMMAND ----------

import subprocess
import sys

from databricks.sdk import WorkspaceClient

# Credencial del propio notebook, por el SDK: funciona en cualquier modo de
# acceso (en clusters Shared, `dbutils.notebook.entry_point` esta bloqueado).
_w = WorkspaceClient()
_HOST = _w.config.host
_TOKEN = _w.config.authenticate()["Authorization"].split(" ", 1)[1]

ENV = {
    **os.environ,
    "DATABRICKS_HOST": _HOST,
    "DATABRICKS_TOKEN": _TOKEN,
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


# Transparencia: el ACCESS MODE real del cluster (no confundir con la Policy).
# SINGLE_USER / DEDICATED = OK; USER_ISOLATION / STANDARD = la copia al driver
# de la celda 4 va a fallar.
try:
    _spark = globals()["spark"]   # lo inyecta el runtime del notebook
    _mode = _w.clusters.get(_spark.conf.get(
        "spark.databricks.clusterUsageTags.clusterId")).data_security_mode
    print("access mode del cluster:", _mode)
except Exception:
    pass

print("listo | python:", sys.version.split()[0])

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. elkjs para el layout (opcional: necesita Node)
# MAGIC El orquestador corre `arrange_all` (elkjs sobre Node) como paso
# MAGIC INFORMATIVO: si no hay Node o no se pudo bajar el bundle, ese paso queda
# MAGIC en WARNING y la carga sigue valida (la migracion deja layout en grilla).
# MAGIC Los DBR suelen traer `node` PELADO (sin npm): se baja el tarball del
# MAGIC registry y se apunta `ELKJS_PATH` a su `elk.bundled.js` (mismo bundle y
# MAGIC version que el front -> mismo layout que "Autoarrange" de la web).

# COMMAND ----------

import shutil
import tarfile
import urllib.request

ELKJS_VERSION = "0.11.1"     # = web-data-model-hub/package.json (mantener en sync)

if not shutil.which("node"):
    print("Node no esta disponible en el cluster: arrange_all quedara en WARNING.")
else:
    elk_dir = os.path.join(WORK, "elk")
    os.makedirs(elk_dir, exist_ok=True)
    bundle = os.path.join(elk_dir, "package", "lib", "elk.bundled.js")
    if not os.path.isfile(bundle):
        try:
            tgz = os.path.join(elk_dir, f"elkjs-{ELKJS_VERSION}.tgz")
            urllib.request.urlretrieve(
                f"https://registry.npmjs.org/elkjs/-/elkjs-{ELKJS_VERSION}.tgz", tgz)
            with tarfile.open(tgz) as t:
                try:
                    t.extractall(elk_dir, filter="data")
                except TypeError:        # Python viejo sin `filter`
                    t.extractall(elk_dir)
        except Exception as exc:
            print(f"No se pudo bajar elkjs del registry ({exc}): arrange en WARNING.")
    if os.path.isfile(bundle):
        ENV["ELKJS_PATH"] = bundle
        print(f"ELKJS_PATH = {bundle}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. DRY-RUN del orquestador
# MAGIC Imprime el plan completo SIN ejecutar nada: archivos agrupados por
# MAGIC proyecto segun `projects.json` de la carpeta (regla general: nombre del
# MAGIC archivo), un gate de calidad por proyecto, los pasos con sus comandos
# MAGIC exactos y — en one-shot — los conteos actuales de la BD (todo lo que se
# MAGIC borraria). Un manifiesto invalido aborta aqui, antes de tocar la BD.
# MAGIC REVISAR ESTO antes de la celda 8.

# COMMAND ----------

ARGS = ["--folder", TARGET] if MODO == "one-shot" else ["--append", TARGET]
if MODO == "one-shot":
    ARGS += ["--base-title", BASE_TITLE]
if MODO == "append" and APPEND_PROJECT:
    ARGS += ["--project", APPEND_PROJECT]
if FORCE:
    ARGS += ["--force"]

run("-m", "scripts.run_migration", *ARGS)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. EJECUTAR
# MAGIC - **one-shot**: exige el widget `confirmar_wipe = si` (doble candado: los
# MAGIC   gates de calidad — uno por proyecto — corren primero y, si alguno falla
# MAGIC   sin `force`, no se borra nada).
# MAGIC - **append**: corre directo (no borra nada).
# MAGIC
# MAGIC La salida completa de cada paso se streamea aqui; el resumen queda en
# MAGIC `migration-reports/run_migration-<ts>.json` y los reportes de decisiones
# MAGIC de cada archivo en `migration-reports/<xml>-<ts>.json`.

# COMMAND ----------

if MODO == "one-shot" and not CONFIRMAR_WIPE:
    print("confirmar_wipe = no -> NO se ejecuta. Revisa el dry-run de arriba y "
          "pon el widget en 'si' para la carga real (BORRA TODA LA BD).")
else:
    run("-m", "scripts.run_migration", *ARGS, "--apply")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9. Cierre
# MAGIC Tras un one-shot OK la web ya abre con `admin` / `admin` (cambiar la clave
# MAGIC al entrar): produccion v1 marcada, data functions sembradas y layout
# MAGIC aplicado. Los usuarios reales se crean desde Admin (la plataforma tiene
# MAGIC su propio padron, no hereda los del workspace).
