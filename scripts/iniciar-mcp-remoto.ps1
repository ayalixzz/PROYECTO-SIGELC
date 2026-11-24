[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [ValidateCount(1, 4)]
    [ValidateRange(1, 2147483647)]
    [int[]]$Entrega,
    [ValidateRange(1024, 65535)]
    [int]$Puerto = 8765,
    [uri]$UrlPublica,
    [uri[]]$RedirectUri = @(),
    [switch]$PermitirPropuestas
)

$ErrorActionPreference = 'Stop'
if (($Entrega | Sort-Object -Unique).Count -ne $Entrega.Count) {
    throw 'Indique IDs de entregas distintos.'
}
$backend = Join-Path (Split-Path -Parent $PSScriptRoot) 'backend'
$python = (Resolve-Path -LiteralPath (Join-Path $backend '.venv\Scripts\python.exe')).Path
$servidor = (Resolve-Path -LiteralPath (Join-Path $backend 'sigelc_mcp_remoto.py')).Path
$lanzador = Join-Path $PSScriptRoot '_lanzar-mcp-remoto.py'
$carpeta = Join-Path $backend 'private_media_demo\.runtime'
New-Item -ItemType Directory -Path $carpeta -Force | Out-Null
$marca = Join-Path $carpeta "mcp-remoto-$Puerto.json"
if (-not $UrlPublica) { $UrlPublica = [uri]"http://127.0.0.1:$Puerto" }

$argumentos = @($marca, $servidor, '--puerto', $Puerto, '--url-publica', $UrlPublica.AbsoluteUri)
foreach ($id in $Entrega) { $argumentos += @('--entrega', $id) }
foreach ($uri in $RedirectUri) { $argumentos += @('--redirect-uri', $uri.AbsoluteUri) }
if ($PermitirPropuestas) { $argumentos += '--permitir-propuestas' }

Write-Host "Iniciando el piloto SIGELC en 127.0.0.1:$Puerto. Ctrl+C lo detiene."
& $python -B $lanzador @argumentos
if ($LASTEXITCODE -ne 0 -and $LASTEXITCODE -ne -1073741510) {
    throw "El proceso MCP remoto terminó con código $LASTEXITCODE."
}
