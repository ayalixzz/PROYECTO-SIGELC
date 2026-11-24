[CmdletBinding()]
param([switch]$CopiarClave, [switch]$CambiarClave)

$ErrorActionPreference = 'Stop'
$backend = Join-Path (Split-Path -Parent $PSScriptRoot) 'backend'
$python = Join-Path $backend '.venv\Scripts\python.exe'
$archivoClave = Join-Path $backend '.demo-clave.dpapi'
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
$argumentos = @('manage.py', 'sembrar_demo', '--settings=config.settings_demo', '--requerimientos-adicionales')
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
try {
    & $python @argumentos
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo provisionar la demo.' }
    if ($generar) {
        # Sin rotación, un usuario existente conserva su contraseña.
        $comprobar = "from django.contrib.auth import authenticate; import os, sys; sys.exit(authenticate(username='abogado-demo', password=os.environ['SIGELC_DEMO_CLAVE_PROVISION']) is None)"
        & $python manage.py shell --settings=config.settings_demo -c $comprobar
        if ($LASTEXITCODE -ne 0) {
            throw 'La cuenta ya tiene una clave. Use la existente o vuelva a ejecutar con -CambiarClave.'
        }
        [IO.File]::WriteAllText($archivoClave, $cifrada)
        Write-Host 'Clave local cifrada con Windows. Use -CopiarClave para ingresar como abogado-demo.'
    }
} finally {
    Pop-Location
    [Environment]::SetEnvironmentVariable($variableClave, $null, 'Process')
}
