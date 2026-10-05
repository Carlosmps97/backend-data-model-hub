# Databricks notebook source
# MAGIC %md
# MAGIC # Carga Erwin (XML) -> Lakebase, desde dentro de Databricks
# MAGIC
# MAGIC Ejecuta el ORQUESTADOR `scripts/run_migration.py` (doc 54) sobre los XML
# MAGIC bajados de ADLS. **No reimplementa nada**: el orquestador invoca los
# MAGIC MISMOS scripts del repo (quality, reset, create_admin, migrate, audit,
# MAGIC seed de data functions, mark_base_version) con las mismas
# MAGIC reglas de merge, `_DUPn` y glosario cruzado.
# MAGIC
# MAGIC Dos modos (widget `modo`):
# MAGIC - **one-shot** (DESTRUCTIVO): baja TODOS los `.xml` de la carpeta raiz
# MAGIC   (recursivo, espejando subcarpetas) y deja la plataforma como un primer
# MAGIC   deployment — borra TODO el schema, recrea los 4 roles de caja, la
# MAGIC   cuenta local `admin` y la whitelist SSO (admins + Modeladores), migra
# MAGIC   los proyectos en CARRILES paralelos (widget `jobs`; los archivos de un
# MAGIC   mismo proyecto siempre en orden), siembra las data functions y marca
# MAGIC   la version base v1 de cada proyecto. El layout de cada canvas, con sus
# MAGIC   colores, textos y cuadros, viene del propio XML (doc 109).
# MAGIC   El proyecto destino sale de la CONVENCION del doc 77, no de un archivo
# MAGIC   declarativo: **cada subcarpeta es un proyecto** que se llama como ella y
# MAGIC   fusiona sus `.xml` (p. ej. `MODELO DDV/` → proyecto «MODELO DDV»), y
# MAGIC   **cada `.xml` suelto en la raiz es su propio proyecto** con el nombre
# MAGIC   del archivo.
# MAGIC - **append** (no destructivo): baja UN archivo y lo SUMA a la BD viva
# MAGIC   (sin reset, sin admin, sin seeds, sin marcador).
# MAGIC
# MAGIC El gate de calidad corre ANTES de borrar nada: si falla, la BD queda
# MAGIC intacta (salvo `force = si`, que continua omitiendo los objetos con ERROR).
# MAGIC
# MAGIC Antes de copiar, el notebook **borra** el area de trabajo del driver
# MAGIC (`/local_disk0/tmp/erwin/carpeta` y `.../append`): ese tmp es acumulativo
# MAGIC entre corridas y los residuos de una corrida previa se levantaban como
# MAGIC PROYECTOS FANTASMA (doc 77 §2).
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

# --- Rendimiento --------------------------------------------------------------
dbutils.widgets.text("jobs", "4", "15. Proyectos en paralelo (1 = secuencial)")

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
JOBS = W("jobs").strip() or "4"
if not JOBS.isdigit() or int(JOBS) < 1:
    raise ValueError(f"`jobs` debe ser un entero >= 1 (llego {JOBS!r}).")

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
    print(f"paralelo : hasta {JOBS} proyecto(s) a la vez")
    print(f"wipe     : {'CONFIRMADO - se borra TODO' if CONFIRMAR_WIPE else 'no confirmado (solo dry-run)'}")
else:
    print(f"archivo  : {adls(ARCHIVO_APPEND)}")
    print(f"proyecto : {APPEND_PROJECT or '(auto: nombre del archivo)'}")
print(f"lakebase : {PGUSER}@{PGHOST} | {LAKEBASE_ENDPOINT} | schema {PGSCHEMA}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Limpiar el driver y materializar los XML
# MAGIC `abfss://` no es un filesystem: `open()` no lo lee. Se baja al disco local
# MAGIC del driver — en one-shot, TODA la carpeta recursivamente, espejando las
# MAGIC subcarpetas (de ahi sale el proyecto de cada archivo). Requiere access
# MAGIC mode **Dedicated**.
# MAGIC
# MAGIC **Primero se BORRA** el area de trabajo (doc 77 §5): sin eso, lo que quedo
# MAGIC de una corrida anterior —una carpeta de ADLS que se renombro, un XML que se
# MAGIC saco— se sumaba a esta y aparecian proyectos que nadie pidio. El guard de
# MAGIC `scripts/databricks/workdir.py` sólo deja borrar dentro del tmp del driver;
# MAGIC el bundle de elkjs (`.../erwin/elk`) se conserva.
# MAGIC
# MAGIC Las copias van en paralelo y se **verifican por tamaño** contra ADLS; lo
# MAGIC que falte se reintenta en serie.

# COMMAND ----------

import sys
import time
from concurrent.futures import ThreadPoolExecutor

if REPO_DIR not in sys.path:
    sys.path.insert(0, REPO_DIR)        # helpers del repo, en el proceso del driver
from scripts.databricks.workdir import limpiar, verificar   # noqa: E402
from scripts.run_migration import project_of                # noqa: E402

WORK = "/local_disk0/tmp/erwin" if os.path.isdir("/local_disk0") else "/tmp/erwin"
LOCAL_ROOT = os.path.join(WORK, "carpeta")
LOCAL_APPEND = os.path.join(WORK, "append")
DESCARGAS_PARALELAS = 4

# --- Limpieza (doc 77 §5) -----------------------------------------------------
# Las DOS areas, siempre: asi un `append` no deja restos que ensucien el proximo
# one-shot. `WORK/elk` no se toca (es el bundle de elkjs, no data de la carga).
for _dir in (LOCAL_ROOT, LOCAL_APPEND):
    _n, _bytes = limpiar(_dir)
    print(f"limpieza: {_dir} -> {_n} archivo(s), {_bytes / 1e6:,.0f} MB borrados")


def walk_xmls(uri: str) -> list:
    """Todos los .xml bajo `uri` (recursivo), via dbutils.fs.ls."""
    out = []
    for f in dbutils.fs.ls(uri):
        if f.isDir():
            out += walk_xmls(f.path)
        elif f.name.lower().endswith(".xml"):
            out.append(f)
    return out


def bajar(uri: str, dst: str) -> str:
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
    return dst


def bajar_todos(items: list) -> None:
    """`items` = [(uri, destino local, bytes en ADLS)]. Copia en paralelo, verifica
    por tamaño y reintenta en serie lo que falte (si `dbutils.fs.cp` no tolerara
    el hilo, el fallback igual completa la copia)."""
    t0 = time.perf_counter()
    try:
        with ThreadPoolExecutor(max_workers=min(DESCARGAS_PARALELAS, len(items))) as ex:
            list(ex.map(lambda it: bajar(it[0], it[1]), items))
    except Exception as exc:
        print(f"copia en paralelo interrumpida ({exc}); reintento en serie lo que falte...")
    esperados = [(dst, size) for _, dst, size in items]
    pendientes = set(verificar(esperados))
    for uri, dst, _ in items:
        if dst in pendientes:
            print(f"  reintento en serie: {dst}")
            bajar(uri, dst)
    faltan = verificar(esperados)
    if faltan:
        raise RuntimeError("copias incompletas o de tamaño distinto al de ADLS:\n  "
                           + "\n  ".join(faltan))
    total = sum(size for _, _, size in items)
    print(f"\n{len(items)} XML materializados · {total / 1e6:,.0f} MB en "
          f"{time.perf_counter() - t0:,.0f}s (verificados por tamaño)")


if MODO == "one-shot":
    base_uri = adls(FOLDER).rstrip("/") + "/"
    files = walk_xmls(base_uri)
    if not files:
        raise ValueError(f"No hay .xml bajo {base_uri} (busqueda recursiva).")
    items = []
    for f in files:
        rel = f.path[len(base_uri):] if f.path.startswith(base_uri) else f.name
        items.append((f.path, os.path.join(LOCAL_ROOT, *rel.split("/")), f.size))
    bajar_todos(items)
    TARGET = LOCAL_ROOT

    # Plan derivado (doc 77): subcarpeta = 1 proyecto; .xml suelto = 1 proyecto.
    proyectos: dict = {}
    for _, dst, size in items:
        rel = os.path.relpath(dst, LOCAL_ROOT).replace(os.sep, "/")
        proyectos.setdefault(project_of(rel), []).append((rel, size))
    print(f"\nPROYECTOS QUE SE VAN A CREAR ({len(proyectos)}):")
    for nombre, archivos in proyectos.items():
        print(f"  «{nombre}»")
        for rel, size in archivos:
            print(f"      · {rel}  ({size / 1e6:,.0f} MB)")
else:
    info = dbutils.fs.ls(adls(ARCHIVO_APPEND))[0]
    TARGET = os.path.join(LOCAL_APPEND, ARCHIVO_APPEND.rsplit("/", 1)[-1])
    bajar_todos([(adls(ARCHIVO_APPEND), TARGET, info.size)])

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
# MAGIC ## 6. Layout de los canvases
# MAGIC Doc 109: ya no se corre el auto-arrange (elkjs/Node no hacen falta). Cada
# MAGIC canvas toma las posiciones de Erwin, agrandadas con la regla fija de la
# MAGIC app (28 / 16.4) y separando solo los bloques que se pisan; trae tambien
# MAGIC los colores, los textos y los cuadros del diagrama.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. DRY-RUN del orquestador
# MAGIC Imprime el plan completo SIN ejecutar nada: archivos agrupados por
# MAGIC proyecto segun la CONVENCION (subcarpeta = 1 proyecto; `.xml` suelto = 1
# MAGIC proyecto), las ETAPAS con sus carriles, los comandos exactos y — en
# MAGIC one-shot — los conteos actuales de la BD (todo lo que se borraria). Dos
# MAGIC nombres de proyecto en colision abortan aqui, antes de tocar la BD.
# MAGIC REVISAR ESTO antes de la celda 8.

# COMMAND ----------

ARGS = ["--folder", TARGET] if MODO == "one-shot" else ["--append", TARGET]
ARGS += ["--jobs", JOBS]
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
# MAGIC Tras un one-shot OK la plataforma ya abre: la cuenta local `admin` entra
# MAGIC con la contraseña definida en `scripts/create_admin.py` (cambiarla al
# MAGIC entrar) y los correos declarados ahi entran por el SSO de Databricks —
# MAGIC `ADMIN_EMAILS` con rol Administrador y `MODELER_EMAILS` con rol Modelador
# MAGIC (las dos listas se editan en ese archivo y pueden quedar vacias). Produccion v1
# MAGIC marcada, data functions sembradas y layout aplicado. Cualquier otro
# MAGIC usuario se crea desde Admin (la plataforma tiene su propio padron, no
# MAGIC hereda los del workspace).
