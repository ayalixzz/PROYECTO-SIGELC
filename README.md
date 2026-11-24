# SIGELC · Revisión del piloto

SIGELC es el Sistema Integral de Gestión y Seguimiento de Liquidaciones Contractuales. Este repositorio contiene el código del piloto, las migraciones, las pruebas y herramientas para generar documentos ficticios. Está preparado para revisar el flujo con datos de ensayo.

## Recorrido para el cliente

1. Acceda como `abogado-demo` y revise el panel de plazos y prioridades.
2. Busque `SIGELC-DEMO-001`. Consulte el estado, el historial, las tareas, los memorandos y los documentos privados del expediente.
3. Desde **Revisión inteligente**, cargue la primera entrega ficticia y ejecute las verificaciones. Cargue la segunda, vuelva a verificar y abra **Comparar entrega**. Los paquetes tienen un número contractual inconsistente en el primero y corregido en el segundo.
4. Revise las citas de los hallazgos. Acepte o edite los que correspondan antes de preparar un borrador Word. Si la entrega queda sin observaciones, use una observación manual sustentada para comprobar la generación.
5. El conector opcional de Claude permite consultar entregas ficticias autorizadas. Con devolución habilitada, el abogado abre una solicitud en SIGELC y envía el mensaje en Claude. Las propuestas recibidas quedan pendientes de revisión.

Los borradores de esta copia usan plantillas de demostración. La aceptación institucional de formatos y reglas se realiza en un entorno autorizado.

## Instalar en Windows

Requisitos de la copia de revisión: Python 3.13, Node.js 24, npm, PostgreSQL local y PowerShell. Desde la raíz del repositorio:

```powershell
Push-Location backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
Pop-Location
Push-Location frontend
npm.cmd ci
Pop-Location
```

En una instalación nueva de PostgreSQL, ejecute esto con un rol administrador desde `psql`. Si el rol o la base ya existen en su equipo, verifique su configuración y conserve los datos.

```sql
CREATE ROLE sigelc_user WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE;
\password sigelc_user
CREATE DATABASE test_sigelc_db OWNER sigelc_user;
SELECT oid FROM pg_database WHERE datname = 'test_sigelc_db';
```

En `backend/.env` configure `DJANGO_SECRET_KEY` con una clave aleatoria propia, `DB_PASSWORD` con la contraseña del rol y `SIGELC_DEMO_DB_OID` con el resultado de la consulta. Los comandos de demo verifican el puerto local 5432, el rol sin privilegios, el nombre de base y su OID. El archivo `.env` se guarda solo en el equipo.

```powershell
Push-Location backend
.\.venv\Scripts\python.exe manage.py migrate --plan --settings=config.settings_demo
.\.venv\Scripts\python.exe manage.py migrate --settings=config.settings_demo
Pop-Location
.\scripts\provisionar-demo.ps1
.\backend\.venv\Scripts\python.exe .\scripts\crear-paquetes-ficticios.py
.\scripts\iniciar-demo.ps1 -Reconstruir
```

La interfaz se abre en [SIGELC local](http://127.0.0.1:5174/). En otra terminal, `.\scripts\provisionar-demo.ps1 -CopiarClave` copia al portapapeles la clave de `abogado-demo`, protegida por Windows en el equipo. Los paquetes generados quedan en `demo-paquetes/`.

## Validación de la copia

```powershell
Push-Location backend
.\.venv\Scripts\python.exe manage.py check --database default --settings=config.settings_demo
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.settings_demo
Pop-Location
Push-Location frontend
npm.cmd run lint
npm.cmd run build
Pop-Location
```

La carpeta `backend/gestion_contractual/tests/` contiene pruebas de reglas, API, administración, seguridad documental y MCP. La cobertura de formatos institucionales utiliza sus propios archivos privados de ensayo. Para revisar desde este repositorio, use las muestras ficticias y haga las pruebas con datos en una base aislada.

Las pruebas de las muestras ficticias pueden ejecutarse sin una conexión a PostgreSQL; basta con configurar la clave local de Django.

```powershell
.\backend\.venv\Scripts\python.exe .\backend\manage.py test gestion_contractual.tests.test_contenido_demo --settings=config.settings
```

## Claude y acceso desde otro equipo

Instale opcionalmente `backend/requirements-mcp.txt` en el entorno virtual para probar MCP. Use las entregas ficticias revisadas y sus IDs en la ficha. `scripts/configurar-claude-mcp.ps1` prepara el conector local. El arranque remoto es independiente y su modo predeterminado es de solo lectura. Para recibir propuestas, el piloto requiere `-PermitirPropuestas` en `iniciar-mcp-remoto.ps1`, los mismos IDs aprobados en ambos lanzadores y consentimiento OAuth adicional.

GitHub aloja el código para revisión. Para que otro equipo abra SIGELC como aplicación web se necesita un servidor de demo separado con Django y PostgreSQL. La aprobación del piloto confirma el recorrido con datos ficticios. La adopción con datos reales requiere importar y conciliar el histórico, validar las reglas institucionales y probar el respaldo y la restauración.
