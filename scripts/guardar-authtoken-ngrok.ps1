[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$backend = Join-Path (Split-Path -Parent $PSScriptRoot) 'backend'
$runtime = Join-Path $backend 'private_media_demo\.runtime'
$null = New-Item -ItemType Directory -Path $runtime -Force
$token = Read-Host 'Introduce el authtoken nuevo de ngrok' -AsSecureString
try {
    if ($token.Length -lt 20) { throw 'El authtoken recibido es demasiado corto.' }
    $archivo = Join-Path $runtime 'ngrok-authtoken.dpapi'
    $token | ConvertFrom-SecureString | Set-Content -LiteralPath $archivo -Encoding utf8 -NoNewline
    Write-Host 'Authtoken cifrado con el perfil actual de Windows.'
} finally {
    $token.Dispose()
}
