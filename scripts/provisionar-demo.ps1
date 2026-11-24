[CmdletBinding()]
param(
    [switch]$CopiarClave,
    [switch]$CambiarClave,
    [switch]$Supabase,
    [string]$Python
)

$ErrorActionPreference = 'Stop'
$backend = Join-Path (Split-Path -Parent $PSScriptRoot) 'backend'
$ejecutable = if ($Python) { (Resolve-Path -LiteralPath $Python).ProviderPath } else { Join-Path $backend '.venv\Scripts\python.exe' }
$nombreClave = if ($Supabase) { '.demo-supabase-clave.dpapi' } else { '.demo-clave.dpapi' }
$archivoClave = Join-Path $backend $nombreClave
$variableClave = 'SIGELC_DEMO_CLAVE_PROVISION'

if ($CopiarClave) {
    if ($CambiarClave) { throw 'Ejecute la rotación y la copia por separado.' }
    $protegida = Get-Content -Raw -LiteralPath $archivoClave | ConvertTo-SecureString
    $clave = [System.Net.NetworkCredential]::new('', $protegida).Password
    Set-Clipboard -Value $clave
    Write-Host 'Clave de abogado-demo copiada. Pégala en SIGELC. Se limpiará el portapapeles en 45 segundos si sigue igual.'
    Start-Sleep -Seconds 45
    if ((Get-Clipboard -Raw) -ceq $clave) { Set-Clipboard -Value '' }
    return
}

$env:PYTHONDONTWRITEBYTECODE = '1'
$perfil = if ($Supabase) { 'config.settings_hospedado' } else { 'config.settings_demo' }
$argumentos = @('manage.py', 'sembrar_demo', "--settings=$perfil", '--requerimientos-adicionales')
if ($Supabase) { $argumentos += @('--plantillas', 'ficticias') }
$generar = $CambiarClave -or -not (Test-Path -LiteralPath $archivoClave)
if ($generar) {
    $bytes = New-Object byte[] 30
    $generador = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generador.GetBytes($bytes) }
    finally { $generador.Dispose() }
    $clave = [Convert]::ToBase64String($bytes)
    $protegida = ConvertTo-SecureString -String $clave -AsPlainText -Force
    $cifrada = ConvertFrom-SecureString -SecureString $protegida
    [Environment]::SetEnvironmentVariable($variableClave, $clave, 'Process')
    $argumentos += @('--password-env', $variableClave)
    if ($CambiarClave) { $argumentos += '--cambiar-clave' }
}

Push-Location $backend
$hostAnterior = $env:RENDER_EXTERNAL_HOSTNAME
try {
    if ($Supabase -and -not $hostAnterior) {
        # La siembra por CLI no escucha HTTP ni publica este hostname.
        $env:RENDER_EXTERNAL_HOSTNAME = 'sigelc-provision.onrender.com'
    }
    & $ejecutable @argumentos
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo provisionar la demo.' }
    if ($generar) {
        # Sin rotación, un usuario existente conserva su contraseña.
        $comprobar = "from django.contrib.auth import authenticate; import os, sys; sys.exit(authenticate(username='abogado-demo', password=os.environ['SIGELC_DEMO_CLAVE_PROVISION']) is None)"
        & $ejecutable manage.py shell "--settings=$perfil" -c $comprobar
        if ($LASTEXITCODE -ne 0) {
            throw 'La cuenta ya tiene una clave. Use la existente o vuelva a ejecutar con -CambiarClave.'
        }
        [IO.File]::WriteAllText($archivoClave, $cifrada)
        Write-Host 'Clave local cifrada con Windows. Use -CopiarClave para ingresar como abogado-demo.'
    }
} finally {
    Pop-Location
    [Environment]::SetEnvironmentVariable($variableClave, $null, 'Process')
    [Environment]::SetEnvironmentVariable('RENDER_EXTERNAL_HOSTNAME', $hostAnterior, 'Process')
}
