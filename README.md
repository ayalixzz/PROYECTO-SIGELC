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

## PostgreSQL de Supabase para revisión

El perfil `config.settings_supabase` conecta Django al **Session pooler** de un proyecto de Supabase con TLS verificado. Mantén el esquema `sigelc_demo` fuera de los esquemas expuestos por la Data API. La cuenta de aplicación `sigelc_demo_app` puede crear y consultar tablas de ese esquema. Django bloquea las pruebas automatizadas contra esta base.

1. Descarga el certificado CA en los ajustes de la base de tu proyecto y copia el **host** y la **referencia del proyecto** desde **Connect → Session pooler**. La referencia aparece después de `postgres.` en el usuario del pooler. [Guía de Supabase](https://supabase.com/docs/guides/database/psql)
2. Desde la raíz del repositorio ejecuta el asistente con tus valores. La contraseña de PostgreSQL se escribe en el prompt oculto. El asistente usa esa contraseña una vez para crear el rol y el esquema, y guarda las credenciales de aplicación en `backend/.env.supabase`, excluido de Git.

   ```powershell
   .\backend\.venv\Scripts\python.exe .\scripts\configurar-supabase.py --host HOST_DEL_POOLER --proyecto REFERENCIA_DEL_PROYECTO
   ```

3. Tras confirmar la conexión, revisa el plan de migraciones y aplícalo al esquema privado. Desde `backend` ejecuta:

   ```powershell
   .\.venv\Scripts\python.exe manage.py check --database default --settings=config.settings_supabase
   .\.venv\Scripts\python.exe manage.py migrate --plan --settings=config.settings_supabase
   .\.venv\Scripts\python.exe manage.py migrate --settings=config.settings_supabase
   .\.venv\Scripts\python.exe manage.py showmigrations --settings=config.settings_supabase
   ```

4. Para abrir una sesión de revisión, crea la cuenta con `manage.py crear_abogado --usuario abogado-demo --settings=config.settings_supabase` y arranca Django con `manage.py runserver 127.0.0.1:8002 --settings=config.settings_supabase`. En otra terminal, desde `frontend`:

   ```powershell
   $env:SIGELC_BACKEND_PORT = '8002'
   npm.cmd run dev -- --port 5175
   ```

   Abre [SIGELC con Supabase](http://127.0.0.1:5175/). El perfil inicial tiene un esquema nuevo. El asistente no importa los expedientes existentes. El acceso local con Supabase y el servicio de Render comparten los documentos privados del esquema `sigelc_demo`. El conector MCP sigue usando el entorno de demo local.

## Demo web en Render

El `Dockerfile` compila React y lo sirve junto a Django mediante HTTPS desde un solo dominio. El servicio gratuito descrito en `render.yaml` utiliza el perfil `config.settings_hospedado`. Supabase conserva los documentos privados de la demo en el esquema `sigelc_demo`, fuera del directorio público de React. Los archivos se descargan desde vistas autenticadas.

1. Aplica las migraciones desde `backend` con `manage.py migrate --settings=config.settings_supabase`. El contenedor comprueba que se hayan aplicado antes de arrancar.
2. Compila el frontend con `npm.cmd run build` desde `frontend`. Luego aprovisiona el abogado y los expedientes ficticios desde la raíz:

   ```powershell
   .\scripts\provisionar-demo.ps1 -Supabase
   .\scripts\provisionar-demo.ps1 -Supabase -CopiarClave
   ```

   La segunda línea coloca la clave generada de `abogado-demo` temporalmente en el portapapeles del usuario que la creó. La copia cifrada queda en el computador, fuera de Git. Guarda los datos de acceso del cliente en un canal privado.
3. Crea el servicio Docker desde el repositorio o configura el Blueprint en Render. Introduce `SIGELC_SUPABASE_HOST`, `SIGELC_SUPABASE_USER` y `SIGELC_SUPABASE_PASSWORD` mediante las variables privadas del servicio. Usa una nueva `SIGELC_SUPABASE_SECRET_KEY` para HTTPS. El certificado CA incluido proviene de la distribución oficial de Supabase. [Conexión SSL](https://supabase.com/docs/guides/database/psql)
4. Abre el dominio HTTPS del servicio, verifica `/api/health/` e inicia sesión como `abogado-demo`. Comprueba un expediente, su historial y un documento ficticio. El plan gratuito puede tardar en reactivarse tras un período sin tráfico. [Límites del plan](https://render.com/docs/free)
