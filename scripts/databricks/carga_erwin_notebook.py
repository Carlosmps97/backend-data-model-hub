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
# MAGIC 3. Los XML accesibles. El notebook resuelve solo, en este orden: Volume
# MAGIC    explicito (widget 0) -> Volume AUTO-detectado que cubra la URI ->
# MAGIC    copia unica al disco del driver. La copia requiere access mode
# MAGIC    **Dedicated** (antes "Single user"). OJO: la **Policy** del cluster
# MAGIC    ("Unrestricted") NO es el access mode; el access mode vive en
# MAGIC    Compute > Edit > Advanced > Access mode, y con el form simple + UC
# MAGIC    suele quedar en "Standard" (antes "Shared") -- exactamente lo que
# MAGIC    dice el error "on Shared cluster".
# MAGIC 4. Tu usuario con rol de Postgres en el proyecto Lakebase.

# COMMAND ----------

# MAGIC %md
# MAGIC ## 0. Widgets (correr una vez; quedan en la barra de arriba)

# COMMAND ----------

# `dbutils` lo inyecta el runtime de Databricks; esta linea es solo para que los
# linters de escritorio no lo marquen como indefinido.
dbutils = globals()["dbutils"]  # type: ignore[assignment]

# --- Origen de los XML --------------------------------------------------------
# Dos formas, segun el modo de acceso del cluster:
#   a) volume_path CON valor -> los XML se leen EN SU SITIO, sin copiar. Es la
#      unica que funciona en clusters "Shared" y es la recomendada. Requiere un
#      Volume de UC (externo) apuntando al ADLS.
#   b) volume_path VACIO -> se arma la URI abfss:// y se materializa en el disco
#      del driver. Necesita cluster en modo "Single user / Dedicated": en Shared
#      Databricks bloquea escribir en file:/local_disk0.
dbutils.widgets.text("volume_path", "", "0. Volume UC (/Volumes/cat/esq/vol/carpeta)")
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

VOLUME = W("volume_path").strip().rstrip("/")
ACCOUNT, CONTAINER = W("storage_account").strip(), W("container").strip()
FOLDER = W("folder_path").strip().strip("/")
PROJECT = W("project").strip()
LAKEBASE_ENDPOINT = W("lakebase_endpoint").strip()
PGHOST, PGUSER = W("pghost").strip(), W("pguser").strip()
PGSCHEMA = W("pgschema").strip() or "dmh"


def origen(nombre: str) -> str:
    """Volume de UC -> ruta tal cual (se lee en su sitio). Si no, URI abfss."""
    if VOLUME:
        return f"{VOLUME}/{nombre}"
    ruta = f"{FOLDER}/{nombre}" if FOLDER else nombre
    return f"abfss://{CONTAINER}@{ACCOUNT}.dfs.core.windows.net/{ruta}"


# Slots opcionales: para una carga incremental deja uno vacio y pon el archivo
# nuevo en el otro. Las celdas de un slot vacio no hacen nada.
_n1, _n2 = W("xml1_name").strip(), W("xml2_name").strip()
XML1_SRC = origen(_n1) if _n1 else ""
XML2_SRC = origen(_n2) if _n2 else ""
RESET_PREVIO = W("reset_previo").strip().lower() == "si"

REPO_DIR = W("repo_dir").strip()
if not REPO_DIR:
    # El notebook vive en <repo>/scripts/databricks/ y su CWD es esa carpeta.
    # (os.getcwd() funciona en cualquier modo de acceso del cluster.)
    REPO_DIR = os.path.abspath(os.path.join(os.getcwd(), "..", ".."))
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
# MAGIC ## 4. Resolver los XML a una ruta que Python pueda abrir
# MAGIC Los scripts leen con `open()` y `abfss://` no es filesystem. Escalera:
# MAGIC 1. Widget `volume_path` con valor -> se leen en su sitio (cero copia).
# MAGIC 2. **Auto-descubrimiento**: se busca en `system.information_schema.volumes`
# MAGIC    un Volume cuya `storage_location` cubra la URI -> en su sitio, cero
# MAGIC    copia, sin que nadie tenga que saberse la ruta.
# MAGIC 3. Copia unica al disco del driver (`dbutils.fs.cp`). Solo posible con
# MAGIC    access mode **Dedicated**; en "Standard/Shared" el error de abajo
# MAGIC    explica exactamente que cambiar.

# COMMAND ----------

# `spark` tambien lo inyecta el runtime (consulta a information_schema).
spark = globals()["spark"]  # type: ignore[assignment]

WORK = "/local_disk0/tmp/erwin" if os.path.isdir("/local_disk0") else "/tmp/erwin"


def _volumen_que_cubre(uri: str) -> str | None:
    """Busca un Volume de UC cuya storage_location cubra la URI abfss y arma
    la ruta /Volumes equivalente (el archivo se lee EN SU SITIO, cero copia).
    Best-effort: sin acceso a system.information_schema devuelve None."""
    try:
        filas = spark.sql(
            "SELECT volume_catalog, volume_schema, volume_name, storage_location "
            "FROM system.information_schema.volumes "
            "WHERE storage_location IS NOT NULL"
        ).collect()
    except Exception:
        return None
    candidatos = []
    for f in filas:
        base = (f.storage_location or "").rstrip("/")
        if base and (uri == base or uri.startswith(base + "/")):
            candidatos.append((len(base), f, base))
    if not candidatos:
        return None
    _, f, base = max(candidatos, key=lambda t: t[0])   # el prefijo mas especifico
    resto = uri[len(base):].lstrip("/")
    return f"/Volumes/{f.volume_catalog}/{f.volume_schema}/{f.volume_name}/{resto}"


def resolver(src: str) -> str:
    # 1) Ruta de filesystem (widget volume_path): en su sitio.
    if src.startswith("/"):
        if not os.path.isfile(src):
            raise FileNotFoundError(f"No existe: {src}")
        print(f"{src}: {os.path.getsize(src) / 1e6:,.0f} MB (en su sitio)")
        return src
    # 2) Un Volume ya cubre esa ruta del ADLS: en su sitio, cero copia.
    via_volume = _volumen_que_cubre(src)
    if via_volume and os.path.isfile(via_volume):
        print(f"{via_volume}: {os.path.getsize(via_volume) / 1e6:,.0f} MB "
              "(Volume detectado automaticamente, cero copia)")
        return via_volume
    # 3) Materializar una vez en el disco del driver (access mode Dedicated).
    os.makedirs(WORK, exist_ok=True)
    dst = os.path.join(WORK, src.rsplit("/", 1)[-1])
    if not os.path.isfile(dst):
        try:
            dbutils.fs.cp(src, "file:" + dst)
        except Exception as exc:
            if "Shared cluster" in str(exc) or "non /Workspace" in str(exc):
                raise RuntimeError(
                    "El cluster corre con ACCESS MODE 'Standard' (antes "
                    "'Shared') y ahi Databricks prohibe escribir al disco del "
                    "driver. OJO: la Policy 'Unrestricted' es otra perilla "
                    "(quien puede configurar el cluster), NO el access mode. "
                    "Arreglo: Compute > Edit > Advanced > Access mode > "
                    "'Dedicated' (antes 'Single user'), reiniciar y reintentar. "
                    "Alternativa sin tocar el cluster: registrar un Volume de "
                    "UC sobre ese ADLS (el paso 2 lo detecta solo, cero copia)."
                ) from exc
            raise
    print(f"{dst}: {os.path.getsize(dst) / 1e6:,.0f} MB")
    return dst


XML1 = resolver(XML1_SRC) if XML1_SRC else None
XML2 = resolver(XML2_SRC) if XML2_SRC else None

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
    "DB_BACKEND": "lakebase",
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
# SINGLE_USER / DEDICATED = puede copiar al driver; USER_ISOLATION / STANDARD
# = solo Volumes.
try:
    _mode = _w.clusters.get(spark.conf.get(
        "spark.databricks.clusterUsageTags.clusterId")).data_security_mode
    print("access mode del cluster:", _mode)
except Exception:
    pass

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
# MAGIC `arrange_all` corre elkjs sobre Node. Los DBR suelen traer `node` PELADO
# MAGIC (sin npm), asi que elkjs no se instala: se baja el tarball del registry
# MAGIC y se usa su `elk.bundled.js` via `ELKJS_PATH` (mismo bundle y version
# MAGIC que el front -> mismo layout que el boton "Autoarrange" de la web).
# MAGIC Si no hay Node o no hay salida al registry, se saltea: la migracion ya
# MAGIC deja layout en grilla y la web puede reordenar canvas por canvas.

# COMMAND ----------

import shutil
import tarfile
import urllib.request

ELKJS_VERSION = "0.11.1"     # = web-data-model-hub/package.json (mantener en sync)

if not shutil.which("node"):
    print("Node no esta disponible en el cluster: saltear el auto-arrange.")
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
            print(f"No se pudo bajar elkjs del registry ({exc}): saltear el auto-arrange.")
    if os.path.isfile(bundle):
        ENV["ELKJS_PATH"] = bundle
        run("scripts/arrange_all.py", "--project", PROJECT)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 13. Version base v1 (cierre)
# MAGIC La migracion escribe directo a publicado y NO crea versiones; sin una
# MAGIC version aplicada, la web bloquea Model. `mark_base_version` crea el
# MAGIC marcador **v1** (approved, 0 cambios) = "todo lo cargado es la base",
# MAGIC deja el baseline de Data Standards (si hace falta) y activa el permiso
# MAGIC de rollback. Correr AL FINAL de la carga completa (con 15 XML: despues
# MAGIC del ultimo). Idempotente: si v1 ya existe, no toca nada.
# MAGIC
# MAGIC Conteos esperados con los 2 archivos: 1 proyecto, 2108 tablas, 96184
# MAGIC columnas, 1630 relaciones, 1932 vistas, 275 canvases, 388 schemas,
# MAGIC 48 folders.

# COMMAND ----------

run("-m", "scripts.mark_base_version")             # dry-run: muestra el plan

# COMMAND ----------

run("-m", "scripts.mark_base_version", "--apply")

# COMMAND ----------

# MAGIC %md
# MAGIC (`reset_to_base_version` NO es parte de la carga: queda para el futuro,
# MAGIC cuando quieras limpiar versiones de prueba y volver a v1.)
