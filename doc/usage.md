# Guía de Uso

## Iniciar el sistema

```bash
# Activar entorno virtual
source .venv/bin/activate

# Ejecutar CLI
python -m src.main
```

Al iniciar, se muestra el banner con la configuración activa:
```
╔══════════════════════════════════════════════════════════════╗
║           🏗️  DATA MODELER AGENT  🏗️                        ║
║     Sistema Multi-Agente de Modelamiento de Datos           ║
║     Microsoft Agent Framework · Python 3.12                 ║
╠══════════════════════════════════════════════════════════════╣
║  Comandos:                                                  ║
║    • Escribe tu solicitud en lenguaje natural                ║
║    • Incluye una ruta .xlsx para cargar tablas               ║
║    • 'exit' o 'quit' para salir                              ║
║    • 'engines' para ver motores soportados                   ║
║    • 'help' para ver esta ayuda                              ║
╚══════════════════════════════════════════════════════════════╝
  Motor por defecto: databricks_sql
  Modelo LLM: gpt-4o
```

---

## Comandos del CLI

| Comando | Descripción |
|---------|-------------|
| `help` | Muestra el banner de ayuda |
| `engines` | Lista los motores de BD soportados |
| `exit` / `quit` / `salir` | Cierra la aplicación |
| *(texto libre)* | Solicitud de modelamiento |

---

## Tipos de input

### 1. Texto libre (lenguaje natural)

Describe las tablas que deseas modelar directamente en español o inglés:

```
┌─ Data Modeler
└─▶ Crea una tabla de productos con: id, nombre, precio, categoría, stock. Motor: postgresql
```

El sistema detecta automáticamente:
- **Motor de BD**: `postgresql`, `mysql`, `sqlserver`, `databricks`, `cosmos`, etc.
- **Relaciones FK**: "tiene FK hacia", "foreign key", "relación con", etc.

### 2. Archivo Excel

Incluye la ruta a un archivo `.xlsx` en tu solicitud:

```
┌─ Data Modeler
└─▶ Modela las tablas del archivo data/sample_input.xlsx para databricks_sql
```

El archivo Excel debe tener:
- **Una pestaña por tabla** (el nombre de la pestaña = nombre de la tabla).
- **Columnas** con headers reconocibles (ver [Tools > parse_excel_file](tools.md#parse_excel_file)).

Ejemplo mínimo de pestaña "Customers":

| Columna | Definición | Tipo |
|---------|-----------|------|
| customer_id | Identificador único del cliente | BIGINT |
| full_name | Nombre completo del cliente | VARCHAR |
| email | Correo electrónico | VARCHAR |

### 3. Texto + Excel combinado

```
┌─ Data Modeler
└─▶ Modela data/sample_input.xlsx para sqlserver. Agrega una relación FK de orders hacia customers por customer_id
```

### 4. Con relaciones explícitas

```
┌─ Data Modeler
└─▶ Crea tablas customers y orders. La tabla orders tiene FK hacia customers por customer_id. Motor: sqlserver
```

---

## Detección automática del motor de BD

El CLI reconoce las siguientes palabras clave para detectar el motor:

| Palabra clave | Motor asignado |
|---------------|----------------|
| `databricks`, `databricks_sql`, `delta` | `databricks_sql` |
| `cosmos`, `cosmosdb` | `cosmosdb` |
| `sqlserver`, `sql server`, `mssql` | `sqlserver` |
| `postgresql`, `postgres` | `postgresql` |
| `mysql` | `mysql` |

Si no se detecta motor, se usa el valor de `DEFAULT_DB_ENGINE` en `.env`.

---

## Salida del sistema

El pipeline produce tres secciones de output:

### 1. Modelo generado

Para cada tabla se muestra:
- **Tabla de columnas** con nombre, tipo, nullable, PK, FK y definición funcional.
- **DDL** sintácticamente correcto para el motor seleccionado.
- **Relaciones** entre tablas (si aplica).

```
┌─────────────────────────────────────────────────────────────┐
│ 📊 Resultado del Modelamiento de Datos                      │
│ Motor de BD: postgresql                                     │
└─────────────────────────────────────────────────────────────┘

          tbl_customers
┌──────────────┬───────────┬──────────┬────┬────┬──────────────────────────┐
│ Columna      │ Tipo      │ Nullable │ PK │ FK │ Definición               │
├──────────────┼───────────┼──────────┼────┼────┼──────────────────────────┤
│ id_customer  │ BIGSERIAL │ ✗        │ PK │    │ Identificador del cliente│
│ name_full    │ VARCHAR   │ ✗        │    │    │ Nombre completo          │
│ email_main   │ VARCHAR   │ ✓        │    │    │ Correo electrónico       │
│ date_created │ TIMESTAMP │ ✗        │    │    │ Fecha de creación        │
│ ...          │           │          │    │    │                          │
└──────────────┴───────────┴──────────┴────┴────┴──────────────────────────┘

┌─ DDL — tbl_customers ──────────────────────────────────────┐
│ CREATE TABLE tbl_customers (                                │
│   id_customer BIGSERIAL PRIMARY KEY,                        │
│   name_full VARCHAR(200) NOT NULL,                          │
│   ...                                                       │
│ );                                                          │
└─────────────────────────────────────────────────────────────┘
```

### 2. Reporte de QA

```
┌───────────────────┐
│ Score de Calidad   │
│       85/100       │
└───────────────────┘

     Columnas Estandarizadas
┌─────────────┬──────────────┬───┬────────────────┬────────────────┐
│ Tabla       │ Original     │ → │ Estandarizado  │ Razón          │
├─────────────┼──────────────┼───┼────────────────┼────────────────┤
│ tbl_orders  │ creation_date│ → │ date_created   │ Match catálogo │
└─────────────┴──────────────┴───┴────────────────┴────────────────┘

     Nuevas Columnas en Catálogo
┌──────────────┬───────────┬────────────┬──────────────────────────┐
│ Columna      │ Tipo      │ Tabla      │ Definición               │
├──────────────┼───────────┼────────────┼──────────────────────────┤
│ price_unit   │ DECIMAL   │ tbl_orders │ Precio unitario producto │
└──────────────┴───────────┴────────────┴──────────────────────────┘
```

### 3. DDL corregido

Si el QA aplicó correcciones de nombres, se muestra el DDL regenerado con los nombres estandarizados.

---

## Conversación multi-turno

Después de recibir resultados, puedes seguir refinando:

```
┌─ Data Modeler
└─▶ Agrega una columna phone_number a la tabla customers

┌─ Data Modeler
└─▶ Cambia el motor a mysql y regenera el DDL

┌─ Data Modeler
└─▶ Agrega una tabla de categorías con relación FK desde productos
```

---

## Generar archivo de ejemplo

```bash
python scripts/create_sample_xlsx.py
```

Crea `data/sample_input.xlsx` con dos pestañas:
- **Customers**: id, nombre, email, teléfono, fecha registro.
- **Orders**: id, id_cliente, monto, fecha, estado.

---

## Test end-to-end

```bash
python scripts/test_pipeline_v2.py
```

Ejecuta el pipeline completo con el archivo de ejemplo y muestra:
1. Tablas parseadas del Excel.
2. Modelo generado por ExecutorAgent.
3. QA report del QAValidatorAgent.
4. Resultado final combinado.

---

## Troubleshooting

### Error de conexión con Azure
```
✗ Error al conectar con Azure AI Foundry: ...
```
- Verificar credenciales en `.env`.
- Verificar que el Service Principal tenga permisos sobre el recurso AI.
- Verificar acceso a internet y firewall.

### Archivo Excel no encontrado
```
✗ Archivo no encontrado: ruta/al/archivo.xlsx
```
- Verificar que la ruta sea correcta (absoluta o relativa a la raíz del proyecto).
- Solo se soportan archivos `.xlsx`.

### JSON inválido en respuesta del LLM
El sistema maneja automáticamente:
- Markdown fences (` ```json ... ``` `).
- Texto preamble antes del JSON.
- Trailing commas.

Si persiste el error, puede indicar que el modelo LLM no está generando JSON válido. Intentar con un modelo más capaz (e.g., `gpt-4o` en vez de `gpt-4o-mini`).

### Sin resultados del workflow
```
✗ Error: El workflow no produjo resultados.
```
- Verificar que el endpoint de AI Foundry esté accesible.
- Verificar logs para errores en llamadas a tools.
- El modelo puede haber excedido tokens o timeout.
