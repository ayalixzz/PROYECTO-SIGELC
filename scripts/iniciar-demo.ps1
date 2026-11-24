[CmdletBinding()]
param(
    [switch]$Reconstruir,
    [ValidateRange(1024, 65535)][int]$PuertoFrontend = 5174,
    [ValidateCount(1, 4)][ValidateRange(1, [int]::MaxValue)][int[]]$ClaudeEntregas
)

$ErrorActionPreference = 'Stop'
$proyecto = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $proyecto 'backend'
$frontend = Join-Path $proyecto 'frontend'
$python = Join-Path $backend '.venv\Scripts\python.exe'
$node = (Get-Command node.exe).Source
$vite = Join-Path $frontend 'node_modules\vite\bin\vite.js'
$env:PYTHONDONTWRITEBYTECODE = '1'
if ($ClaudeEntregas) {
    if (($ClaudeEntregas | Sort-Object -Unique).Count -ne $ClaudeEntregas.Count) {
        throw 'Los IDs de entregas para Claude deben ser distintos.'
    }
    $env:SIGELC_CLAUDE_ENTREGAS = $ClaudeEntregas -join ','
}

Push-Location $backend
try {
    & $python manage.py check --database default --settings=config.settings_demo
    if ($LASTEXITCODE -ne 0) { throw 'No se pudo validar el backend aislado.' }
} finally { Pop-Location }

if ($Reconstruir -or -not (Test-Path -LiteralPath (Join-Path $frontend 'dist\index.html'))) {
    Push-Location $frontend
    try {
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'No se pudo compilar React.' }
    } finally { Pop-Location }
}

$django = $null
$registros = Join-Path $backend 'private_media_demo\.runtime'
New-Item -ItemType Directory -Path $registros -Force | Out-Null
$errorDjango = Join-Path $registros 'django-error.log'
$salidaDjango = Join-Path $registros 'django-salida.log'
try {
    $django = Start-Process -FilePath $python -WorkingDirectory $backend -WindowStyle Hidden -PassThru `
        -RedirectStandardError $errorDjango -RedirectStandardOutput $salidaDjango -ArgumentList @(
        'manage.py', 'runserver', '127.0.0.1:8001', '--noreload', '--settings=config.settings_demo'
    )
    $listo = $false
    foreach ($intento in 1..24) {
        if ($django.HasExited) { throw "Django no pudo arrancar en el puerto 8001. Consulta $errorDjango." }
        try {
            $salud = Invoke-RestMethod 'http://127.0.0.1:8001/api/health/' -TimeoutSec 2
            if ($salud.status -eq 'ok') { $listo = $true; break }
        } catch { Start-Sleep -Milliseconds 250 }
    }
    if (-not $listo) { throw 'El backend aislado no respondió.' }
    Write-Host "SIGELC local en http://127.0.0.1:${PuertoFrontend}/ · Ctrl+C termina la sesión."
    Push-Location $frontend
    try {
        & $node $vite preview --port $PuertoFrontend
        if ($LASTEXITCODE -ne 0) { throw 'No se pudo iniciar el frontend compilado.' }
    }
    finally { Pop-Location }
} finally {
    if ($django -and -not $django.HasExited) { Stop-Process -Id $django.Id -ErrorAction SilentlyContinue }
}
