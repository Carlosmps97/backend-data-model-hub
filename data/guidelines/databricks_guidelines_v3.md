**LINEAMIENTOS DE MODELAMIENTO DE DATOS**

Databricks Data Modeling Guidelines

Versión 1.0

Febrero 2026

## Tabla de Contenidos

## 1. Introducción

Este documento establece los lineamientos estándar para el modelamiento de tablas en el entorno Databricks de la organización. Estos estándares buscan garantizar consistencia, escalabilidad y mantenibilidad a largo plazo en todos los desarrollos de datos.

### 1.1 Objetivos

Los presentes lineamientos tienen como objetivos principales: (a) establecer un lenguaje común y agnóstico para la denominación de objetos de datos, (b) facilitar la comprensión y navegación de modelos de datos por parte de múltiples equipos, (c) garantizar la trazabilidad y auditoría de información, (d) permitir la automatización y estandarización de procesos ETL, y (e) asegurar la escalabilidad del modelo ante futuros requerimientos.

### 1.2 Alcance

Estos lineamientos aplican a todas las tablas creadas en el entorno Databricks, independientemente de su capa (bronze, silver, gold), periodicidad o tipo de dato. Incluye tablas Delta Lake, vistas materializadas y tablas externas.

## 2. Convenciones de Nomenclatura para Tablas

### 2.1 Estructura General

Todas las tablas seguirán la siguiente estructura de nomenclatura:

**[PREFIJO\_TIPO][PERIODICIDAD]\_[NOMBRE\_DESCRIPTIVO]**

Donde cada componente tiene reglas específicas detalladas en las siguientes secciones. Los nombres deben ser completamente en inglés, minúsculas y utilizar guion bajo como separador.

### 2.2 Prefijos de Tipo de Tabla

El prefijo de tipo identifica la naturaleza funcional de la tabla dentro del modelo de datos.

| **Prefijo**   | **Tipo**   | **Descripción**                                                                 |
|---------------|------------|---------------------------------------------------------------------------------|
| **h**         | Historical | Tablas que almacenan histórico completo de datos transaccionales                |
| **m**         | Master     | Tablas maestras o de dimensiones que contienen datos de referencia              |
| **f**         | Facts      | Tablas de hechos con métricas y medidas agregadas                               |
| **s**         | Snapshot   | Fotografías puntuales del estado de datos en un momento específico              |
| **a**         | Aggregated | Tablas con datos agregados y consolidados para reportería                       |
| **t**         | Temporary  | Tablas temporales para procesamiento intermedio (deben eliminarse post-proceso) |
| **c**         | Calculated | Tablas con campos calculados o métricas derivadas                               |
| **l**         | Log        | Tablas de auditoría y registro de eventos del sistema                           |

### 2.3 Indicadores de Periodicidad

El indicador de periodicidad establece la frecuencia con la que se actualiza la información en la tabla.

| **Código**   | **Periodicidad**   | **Uso**                                  |
|--------------|--------------------|------------------------------------------|
| **d**        | Daily              | Actualización diaria                     |
| **w**        | Weekly             | Actualización semanal                    |
| **m**        | Monthly            | Actualización mensual                    |
| **h**        | Hourly             | Actualización cada hora                  |
| **rt**       | Real-time          | Actualización en tiempo real (streaming) |
| **e**        | Eventually         | Actualización eventual o bajo demanda    |
| **s**        | Static             | Datos estáticos que no se actualizan     |

### 2.4 Ejemplos de Nomenclatura de Tablas

| **Nombre de Tabla**         | **Descripción**                             |
|-----------------------------|---------------------------------------------|
| **hd\_identity**            | Histórico diario de identidades de clientes |
| **fm\_sales**               | Hechos mensuales agregados de ventas        |
| **ms\_customer**            | Maestro estático de clientes                |
| **sw\_inventory\_snapshot** | Snapshot semanal del estado de inventario   |
| **ad\_revenue\_summary**    | Resumen diario agregado de ingresos         |
| **lrt\_api\_events**        | Log en tiempo real de eventos de API        |

## 3. Convenciones de Nomenclatura para Columnas

### 3.1 Reglas Generales

Los nombres de columnas deben seguir las siguientes reglas: (a) completamente en minúsculas, (b) en idioma inglés, (c) sin espacios ni caracteres especiales excepto guion bajo, (d) descriptivos y concisos, (e) usar prefijos estándar según el tipo de dato.

### 3.2 Prefijos Estándar por Tipo de Dato

| **Prefijo**   | **Tipo de Dato**   | **Ejemplos**                            |
|---------------|--------------------|-----------------------------------------|
| **cod**       | Códigos            | codcustomer, codproduct, codtransaction |
| **id**        | Identificadores    | iduseruuid, idorder, idsession          |
| **flg**       | Flags / Booleanos  | flgactive, flgdeleted, flgpremium       |
| **amnt**      | Montos             | amntsales, amnttax, amntdiscount        |
| **qty**       | Cantidades         | qtyitems, qtyunits, qtystock            |
| **pct**       | Porcentajes        | pctdiscount, pctgrowth, pctcompletion   |
| **dt**        | Fechas             | dtcreation, dtupdate, dtexpiration      |
| **ts**        | Timestamps         | tsevent, tsprocessed, tsingested        |
| **desc**      | Descripciones      | descproduct, descstatus, descerror      |
| **nm**        | Nombres            | nmfirst, nmlast, nmfull, nmcompany      |
| **txt**       | Textos largos      | txtcomments, txtdescription, txtbody    |
| **cnt**       | Contadores         | cntvisits, cntattempts, cntrecords      |
| **idx**       | Índices/Posiciones | idxsequence, idxrank, idxposition       |
| **url**       | URLs               | urlwebsite, urlimage, urlapi            |
| **json**      | Datos JSON         | jsonmetadata, jsonconfig, jsonpayload   |

### 3.3 Columnas Obligatorias de Auditoría

Todas las tablas deben incluir las siguientes columnas de auditoría mínimas para garantizar trazabilidad y control de registros activos:

| **Columna**   | **Propósito**                                            |
|---------------|----------------------------------------------------------|
| **tsupdated** | Timestamp de última actualización                        |
| **flgactive** | Indicador de si el registro está activo (borrado lógico) |

### 3.4 Convenciones para Columnas con Múltiples Conceptos

En el contexto bancario y financiero es común encontrar campos que representan más de un concepto simultáneamente (ej. un monto junto con su moneda, un documento junto con su tipo). La convención para estos casos concatena directamente los segmentos después del prefijo, sin separadores, en el siguiente orden jerárquico:

**[PREFIJO] + [CONCEPTO\_PRINCIPAL] + [CALIFICADOR] + [SUBTIPO]**

No todos los segmentos son obligatorios: solo se añaden los necesarios para hacer el nombre unívoco. La longitud máxima recomendada del nombre completo es 50 caracteres.

#### Definición de cada segmento

**PREFIJO —** Sigue la tabla de prefijos estándar de la sección 3.2 (ej. amnt, cod, flg, dt, txt). Siempre presente, nunca se omite.

**CONCEPTO PRINCIPAL —** Es la entidad o acción central que el campo mide o describe; responde a la pregunta »¿qué representa este campo?«. Se escribe como **una sola palabra completa en inglés** , en minúsculas, sin abreviar (ej. debt, balance, phone, address, score, interest, channel). Es el segmento de mayor peso semántico y no debe omitirse.

**CALIFICADOR —** Precisa o restringe el concepto principal; responde a »¿de qué tipo, en qué dimensión o bajo qué clasificación?« (ej. moneda, tipo de cuenta, canal, segmento). Su forma de construcción sigue esta jerarquía: (1) si existe un código estándar internacional se usa ese código (ej. ISO 4217 para monedas: pen, usd); (2) si no existe código estándar, se usa la **palabra completa en inglés** , en minúsculas, sin abreviar (ej. home, primary, credit, savings, origin). Es opcional si el concepto principal ya es suficientemente específico.

**SUBTIPO —** Agrega una tercera dimensión cuando el calificador por sí solo no hace el nombre unívoco; responde a »¿con qué periodicidad, en qué dirección o bajo qué contexto adicional?«. Sigue las mismas reglas que el calificador: código estándar si existe, o palabra completa en inglés si no (ej. annual, monthly, destination). Su uso es estrictamente opcional y solo se incluye cuando es necesario para evitar ambigüedad.

#### Ejemplos descompuestos

amntdebtpen → **amnt** (prefijo) + **debt** (concepto) + **pen** (calificador ISO) → monto de deuda en soles

amntdebtusd → **amnt** + **debt** + **usd** → monto de deuda en dólares

pctinterestannual → **pct** + **interest** + **annual** (subtipo) → tasa de interés anual

txtaddresshome → **txt** + **address** + **home** → dirección domiciliaria

amntbalancecreditpen → **amnt** + **balance** + **credit** + **pen** → saldo de línea de crédito en soles

txtphoneprimary → **txt** + **phone** + **primary** → teléfono principal (txt porque puede incluir «+» y guiones)

coddocidentity → **cod** + **doc** + **identity** → número de documento de identidad ( **doc** es la única abreviatura admitida, universalmente reconocida)

#### Consideraciones adicionales

**Abreviaciones:** Como regla general no se abrevia. Las únicas excepciones son los códigos de estándar internacional (ej. ISO de monedas) y el caso doc como forma corta de document. Cualquier nueva abreviatura debe ser aprobada por el equipo y registrada en el glosario (sección 8.1).

**Longitud máxima:** El nombre completo de la columna no debe superar los 50 caracteres. Si al aplicar la convención se supera este límite, se deben revisar los segmentos para encontrar una forma más concisa sin perder legibilidad, y el caso debe documentarse.

## 4. Tipos de Datos Estándar

Para garantizar consistencia y optimización de almacenamiento, se establecen los siguientes tipos de datos estándar:

| **Tipo de Dato**            | **Tipo Databricks**    | **Uso Recomendado**                                                                                                                                |
|-----------------------------|------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------|
| Códigos alfanuméricos       | **STRING**             | Códigos de cliente, producto, transacción                                                                                                          |
| Identificadores únicos      | **STRING (UUID)**      | IDs generados por sistema, correlación                                                                                                             |
| Enteros pequeños            | **INT**                | Cantidades, contadores, índices                                                                                                                    |
| Enteros grandes             | **BIGINT**             | Timestamps en epoch, IDs numéricos                                                                                                                 |
| Montos monetarios           | **DECIMAL(18,2)**      | Precios, montos de ventas, impuestos                                                                                                               |
| Porcentajes                 | **DECIMAL(5,4)**       | Tasas, descuentos (almacenar como 0.1525 = 15.25%)                                                                                                 |
| Booleanos                   | **BOOLEAN**            | Flags, indicadores (true/false)                                                                                                                    |
| Fechas                      | **DATE**               | Fechas sin componente de tiempo                                                                                                                    |
| Fecha y hora                | **TIMESTAMP**          | Eventos con fecha y hora exacta, auditoría                                                                                                         |
| Datos estructurados         | **STRUCT**             | Objetos anidados con esquema conocido                                                                                                              |
| Arrays                      | **ARRAY&lt;TYPE&gt;**  | Listas de elementos del mismo tipo                                                                                                                 |
| Mapas clave-valor           | **MAP&lt;K,V&gt;**     | Pares clave-valor dinámicos                                                                                                                        |
| JSON semi-estructurado      | **VARIANT**            | JSON anidado complejo con esquema variable. Nativo de Databricks; más flexible que STRUCT para payloads dinámicos como respuestas de API y eventos |
| Números de punto flotante   | **FLOAT / DOUBLE**     | Variables científicas o de ML (scores, probabilidades continuas). Evitar para montos monetarios; preferir DECIMAL                                  |
| Enteros pequeños (8/16 bit) | **TINYINT / SMALLINT** | Flags numéricos y códigos de estado de rango reducido. Optimizan almacenamiento en tablas de alta cardinalidad                                     |

## 5. Buenas Prácticas de Particionamiento

### 5.1 Criterios de Particionamiento

El particionamiento no es un lineamiento obligatorio sino un conjunto de buenas prácticas y recomendaciones para optimizar el rendimiento de las consultas. Su aplicación debe evaluarse caso por caso según el volumen de datos y los patrones de acceso. Los criterios a considerar incluyen: (a) volumen de datos por partición (objetivo 500MB a 1GB), (b) cardinalidad de la columna de partición, (c) frecuencia de consultas por partición, (d) crecimiento histórico de datos.

### 5.2 Columnas de Partición Estándar

| **Columna**         | **Uso Recomendado**                                                 |
|---------------------|---------------------------------------------------------------------|
| **dtpartition**     | Partición por fecha (YYYY-MM-DD) para datos históricos diarios      |
| **yearpartition**   | Partición por año (YYYY) para datos históricos de largo plazo       |
| **monthpartition**  | Partición por mes (YYYY-MM) para agregaciones mensuales             |
| **regionpartition** | Partición por región geográfica cuando las consultas son por región |

## 6. Buenas Prácticas y Estándares de Calidad

### 6.1 Documentación de Tablas

Todas las tablas deben incluir: (a) descripción clara del propósito en propiedades de tabla (COMMENT), (b) descripción de cada columna, (c) definición de claves primarias y foráneas cuando aplique, (d) información sobre el proceso de carga (frecuencia, fuente), (e) responsable o equipo propietario de la tabla.

### 6.2 Optimización de Almacenamiento

Para optimizar el almacenamiento: (a) utilizar OPTIMIZE regularmente en tablas Delta, (b) configurar Z-ORDER en columnas de alta cardinalidad frecuentemente filtradas, (c) ejecutar VACUUM para eliminar archivos obsoletos (considerando retention period), (d) revisar tamaño de archivos con DESCRIBE DETAIL.

### 6.3 Control de Calidad de Datos

Implementar validaciones en cada capa: (a) validación de NOT NULL en campos críticos, (b) validación de rangos y formatos, (c) checks de duplicados en claves primarias, (d) reconciliación de conteos entre capas, (e) alertas automáticas en caso de anomalías.

### 6.4 Versionamiento y Esquemas

Para evolución de esquemas: (a) utilizar merge schema solo cuando sea necesario, (b) documentar todos los cambios de esquema, (c) considerar compatibilidad hacia atrás, (d) versionar esquemas críticos, (e) comunicar cambios al equipo.

### 6.5 Seguridad y Gobernanza

Aplicar políticas de seguridad: (a) uso de Unity Catalog para control de acceso, (b) enmascaramiento de datos sensibles (PII), (c) registro de accesos mediante audit logs, (d) clasificación de datos según nivel de sensibilidad, (e) cumplimiento con regulaciones (GDPR, CCPA).

## 7. Casos de Uso y Ejemplos Completos

### 7.1 Ejemplo: Tabla de Transacciones Históricas

**Tabla:** hd\_sales\_transactions

Esta tabla almacena el histórico completo de transacciones de ventas con actualización diaria.

| **Columna**   | **Tipo**      | **Descripción**                |
|---------------|---------------|--------------------------------|
| idtransaction | STRING        | ID único de transacción (UUID) |
| codcustomer   | STRING        | Código de cliente              |
| codproduct    | STRING        | Código de producto             |
| amntsales     | DECIMAL(18,2) | Monto total de venta           |
| qtyitems      | INT           | Cantidad de items              |
| dttransaction | TIMESTAMP     | Fecha y hora de transacción    |
| dtpartition   | DATE          | Partición por fecha            |
| tscreated     | TIMESTAMP     | Timestamp de creación          |

### 7.2 Ejemplo: Tabla Maestra de Productos

**Tabla:** ms\_product

Tabla maestra estática que contiene el catálogo de productos.

| **Columna**   | **Tipo**      | **Descripción**               |
|---------------|---------------|-------------------------------|
| codproduct    | STRING        | Código único de producto (PK) |
| nmproduct     | STRING        | Nombre del producto           |
| descproduct   | STRING        | Descripción del producto      |
| codcategory   | STRING        | Código de categoría           |
| amntprice     | DECIMAL(18,2) | Precio unitario               |
| flgactive     | BOOLEAN       | Producto activo/inactivo      |
| tscreated     | TIMESTAMP     | Timestamp de creación         |
| tsupdated     | TIMESTAMP     | Timestamp de actualización    |

## 8. Anexos

### 8.1 Glosario de Términos

**Delta Lake:** Capa de almacenamiento open-source que proporciona transacciones ACID sobre data lakes.

**Z-ORDER:** Técnica de optimización que co-localiza datos relacionados en el mismo conjunto de archivos.

**Unity Catalog:** Solución de gobernanza unificada para gestión de datos y AI en Databricks.

**Medallion Architecture:** Patrón de diseño de datos con capas Bronze (raw), Silver (refined) y Gold (aggregated).

### 8.2 Referencias

• Databricks Delta Lake Documentation

• Best Practices for Data Engineering on Databricks

• Unity Catalog Governance Guide

### 8.3 Control de Versiones

|   **Versión** | **Fecha**    | **Autor**            | **Cambios**                                                                        |
|---------------|--------------|----------------------|------------------------------------------------------------------------------------|
|           1.0 | Febrero 2026 | Carlos Perez Salcedo | Versión inicial del documento. Data Engineer, Programa IA — carlosperez@bcp.com.pe |

*Fin del Documento*