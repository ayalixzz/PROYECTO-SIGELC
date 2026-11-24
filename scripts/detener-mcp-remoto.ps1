[CmdletBinding()]
param([ValidateRange(1024, 65535)][int]$Puerto = 8765)

$ErrorActionPreference = 'Stop'
$backend = Join-Path (Split-Path -Parent $PSScriptRoot) 'backend'
$marca = Join-Path $backend "private_media_demo\.runtime\mcp-remoto-$Puerto.json"
$contenido = Get-Content -LiteralPath $marca -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
if (-not $contenido) {
    Write-Host 'No hay una instancia MCP remota registrada en ese puerto.'
    return
}
$datos = $contenido | ConvertFrom-Json
if ($datos.instancia -cnotmatch '^[0-9a-f]{32}$') {
    throw 'El registro de la instancia MCP remota no es válido.'
}
$senal = Join-Path (Split-Path -Parent $marca) "mcp-remoto-$Puerto-$($datos.instancia).stop"
[IO.File]::WriteAllText($senal, '')
$fin = [DateTime]::UtcNow.AddSeconds(10)
while ([DateTime]::UtcNow -lt $fin) {
    $actual = Get-Content -LiteralPath $marca -Raw -Encoding UTF8 -ErrorAction SilentlyContinue
    if (-not $actual -or ($actual | ConvertFrom-Json).instancia -cne $datos.instancia) {
        Write-Host "Instancia MCP remota detenida en el puerto $Puerto."
        return
    }
    Start-Sleep -Milliseconds 250
}
throw 'La instancia no confirmó el cierre. Compruebe la terminal del lanzador.'
