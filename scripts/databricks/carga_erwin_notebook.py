# Databricks notebook source
# MAGIC %md
# MAGIC # Carga Erwin (XML) -> Lakebase, desde dentro de Databricks
# MAGIC
# MAGIC Plan B de la doc `plan-implementacion/35-MIGRACION-A-OTRO-DATABRICKS.md`:
# MAGIC se usa cuando el endpoint Lakebase NO es alcanzable desde la laptop
# MAGIC (el front-end "service direct" acepta el TCP y descarta el trafico).
# MAGIC Desde un cluster del mismo workspace si se llega.
# MAGIC
# MAGIC **Este notebook no reimplementa nada**: invoca los MISMOS scripts del repo
# MAGIC (`scripts.erwin_migration.*`), con las mismas reglas de merge, limpieza y
# MAGIC prioridad. Solo cambia desde donde se ejecutan.
# MAGIC
# MAGIC ## Prerequisitos
# MAGIC 1. **Cluster classic** (no serverless), DBR con Python 3.10+, single node
# MAGIC    con 16 GB de driver alcanza (el XML grande pide ~0.5 GB de RAM).
# MAGIC 2. **El repo del backend en el workspace**: Git folder apuntando a
# MAGIC    `back-dmh-01`, o subido con `databricks workspace import-dir`.
# MAGIC 3. **Los 2 XML en ADLS / Volume**, accesibles con `dbutils.fs`.
# MAGIC 4. Tu usuario con rol de Postgres en el proyecto Lakebase.
# MAGIC
# MAGIC ## Antes de nada: verifica que desde este cluster SI se llega

# COMMAND ----------

import socket
import struct

PGHOST_TEST = "ep-morning-fog-e8ms4ebo.database.centralus.azuredatabricks.net"

s = socket.create_connection((PGHOST_TEST, 5432), 15)
s.sendall(struct.pack("!ii", 8, 80877103))   # SSLRequest de Postgres
print("respuesta:", s.recv(1), "  (b'S' = el camino sirve)")
s.close()

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. Dependencias
# MAGIC Instala las del repo en el entorno del notebook y reinicia Python.
# MAGIC Al reiniciar se pierden las variables: por eso los parametros van DESPUES.

# COMMAND ----------

# MAGIC %pip install -r /Workspace/Repos/<tu-usuario>/back-dmh-01/requirements.txt
# MAGIC %restart_python

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Parametros (lo unico que editas)

# COMMAND ----------

REPO_DIR = "/Workspace/Repos/<tu-usuario>/back-dmh-01"      # raiz del repo backend

# Origen de los XML (ADLS con abfss://, o /Volumes/... si es un Volume de UC)
XML1_SRC = "abfss://<container>@<cuenta>.dfs.core.windows.net/<ruta>/DDV - CPYBCA.xml"
XML2_SRC = "abfss://<container>@<cuenta>.dfs.core.windows.net/<ruta>/DDV Modelo de Datos Fisico Otros V0.214.xml"

PROJECT = "Modelo de Datos DDV_FISICO"                      # proyecto destino (mismo para los 2)

# Conexion a Lakebase
LAKEBASE_ENDPOINT = "projects/lkbs-model-hub/branches/production/endpoints/primary"
PGHOST = "ep-morning-fog-e8ms4ebo.database.centralus.azuredatabricks.net"
PGUSER = "carlosperez@bcp.com.pe"                           # el Role que muestra la consola Lakebase
PGDATABASE = "databricks_postgres"
LAKEBASE_PGSCHEMA = "dmh"

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Copiar los XML al disco local del driver
# MAGIC Los scripts leen con `open()` normal: necesitan una ruta de filesystem.
# MAGIC Copiar al disco local es mas rapido y seguro que leer por FUSE.
# MAGIC (Si tus XML ya estan en un Volume de UC, puedes saltarte esta celda y
# MAGIC apuntar XML1/XML2 directo a `/Volumes/...`.)

# COMMAND ----------

import os
import shutil

WORK = "/local_disk0/tmp/erwin" if os.path.isdir("/local_disk0") else "/tmp/erwin"
os.makedirs(WORK, exist_ok=True)

XML1 = os.path.join(WORK, "xml1.xml")
XML2 = os.path.join(WORK, "xml2.xml")

for src, dst in ((XML1_SRC, XML1), (XML2_SRC, XML2)):
    if src.startswith("/Volumes/") or src.startswith("/dbfs/"):
        shutil.copyfile(src, dst)
    else:
        dbutils.fs.cp(src, "file:" + dst)                   # noqa: F821 (dbutils)
    print(f"{dst}: {os.path.getsize(dst) / 1e6:,.0f} MB")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Entorno + helper que ejecuta los scripts
# MAGIC El token del notebook autentica ante el workspace; el backend lo usa para
# MAGIC acunar el password de Postgres (dura ~1 h y se renueva solo).

# COMMAND ----------

import subprocess
import sys

_ctx = dbutils.notebook.entry_point.getDbutils().notebook().getContext()   # noqa: F821
DATABRICKS_HOST = "https://" + _ctx.browserHostName().get()
DATABRICKS_TOKEN = _ctx.apiToken().get()

ENV = {
    **os.environ,
    "DB_BACKEND": "lakebase",
    "DATABRICKS_HOST": DATABRICKS_HOST,
    "DATABRICKS_TOKEN": DATABRICKS_TOKEN,
    "LAKEBASE_ENDPOINT": LAKEBASE_ENDPOINT,
    "PGHOST": PGHOST,
    "PGPORT": "5432",
    "PGUSER": PGUSER,
    "PGDATABASE": PGDATABASE,
    "PGSSLMODE": "require",
    "LAKEBASE_PGSCHEMA": LAKEBASE_PGSCHEMA,
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


print("host:", DATABRICKS_HOST, "| python:", sys.version.split()[0])

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Usuario para poder entrar a la web
# MAGIC Crea `admin` / `admin` y los 4 roles. Cambia esa clave en el primer login.

# COMMAND ----------

run("scripts/create_admin.py")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. XML 1 (CPYBCA): gate de calidad -> cruce vs BD -> dry-run -> apply
# MAGIC El orden importa: CPYBCA primero, Otros despues (el desempate por uso
# MAGIC actualiza sobre lo ya cargado).

# COMMAND ----------

run("-m", "scripts.erwin_migration.quality", XML1)

# COMMAND ----------

run("-m", "scripts.erwin_migration.crosscheck", XML1)

# COMMAND ----------

run("-m", "scripts.erwin_migration.migrate", XML1, "--project", PROJECT)

# COMMAND ----------

run("-m", "scripts.erwin_migration.migrate", XML1, "--project", PROJECT, "--apply")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. XML 2 (Otros V0.214): mismo flujo, MISMO proyecto
# MAGIC Aca actua el merge multi-archivo (adopcion por clave natural, conflicto
# MAGIC por score de uso, dedup de FKs, fusion de folders/canvases homonimos).

# COMMAND ----------

run("-m", "scripts.erwin_migration.quality", XML2)

# COMMAND ----------

run("-m", "scripts.erwin_migration.crosscheck", XML2)

# COMMAND ----------

run("-m", "scripts.erwin_migration.migrate", XML2, "--project", PROJECT)

# COMMAND ----------

run("-m", "scripts.erwin_migration.migrate", XML2, "--project", PROJECT, "--apply")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Validacion de la data cargada

# COMMAND ----------

run("-m", "scripts.audit_data_consistency")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 9. Reglas base de DDL Export (Data Standards)
# MAGIC El glosario, dominios y definiciones UDP ya entraron con `migrate`.
# MAGIC Esto siembra el ruleset de exportacion (8 reglas + 2 lookups).

# COMMAND ----------

run("-m", "scripts.seed_ddl_export_rules")            # dry-run

# COMMAND ----------

run("-m", "scripts.seed_ddl_export_rules", "--apply")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 10. Layout de canvases (opcional, necesita Node)
# MAGIC `arrange_all` corre elkjs. Si el cluster no trae Node, saltea esta parte:
# MAGIC la migracion ya deja un layout en grilla y el boton "Autoarrange" de la
# MAGIC web reordena canvas por canvas.

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
# MAGIC ## 11. Cierre
# MAGIC En la web: crear una version vacia "v1 Base" (submit + approve, 0 cambios)
# MAGIC y recien despues correr `reset_to_base_version` si quieres el baseline de
# MAGIC Data Standards y el boton Restore.
# MAGIC
# MAGIC Conteos esperados con los 2 archivos cargados: 1 proyecto, 2108 tablas,
# MAGIC 96184 columnas, 1630 relaciones, 1932 vistas, 275 canvases, 388 schemas.
