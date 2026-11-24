[CmdletBinding()]
param([switch]$Registrar)

$ErrorActionPreference = 'Stop'
$backend = Join-Path (Split-Path -Parent $PSScriptRoot) 'backend'
$python = (Resolve-Path -LiteralPath (Join-Path $backend '.venv\Scripts\python.exe')).Path
$servidor = (Resolve-Path -LiteralPath (Join-Path $backend 'sigelc_mcp.py')).Path
& $python -c 'import mcp, win32crypt, django'
if ($LASTEXITCODE -ne 0) { throw 'Instale backend/requirements-mcp.txt en el entorno virtual.' }
& $python $servidor --verificar
if ($LASTEXITCODE -ne 0) { throw 'No se confirmó el aislamiento de la base de la demo.' }

$carpeta = Join-Path $env:APPDATA 'Claude'
$archivo = Join-Path $carpeta 'claude_desktop_config.json'
$entrada = [pscustomobject]@{command = $python; args = @($servidor)}
$existe = Test-Path -LiteralPath $archivo -PathType Leaf
$previo = if ($existe) { Get-Content -Raw -Encoding UTF8 -LiteralPath $archivo } else { '{}' }
$config = ConvertFrom-Json -InputObject $previo
if ($config -isnot [pscustomobject]) { throw 'La raíz de la configuración debe ser un objeto JSON.' }
if (-not $config.PSObject.Properties['mcpServers']) {
    $config | Add-Member NoteProperty mcpServers ([pscustomobject]@{})
}
if ($config.mcpServers -isnot [pscustomobject]) { throw 'mcpServers debe ser un objeto JSON.' }
if (-not $Registrar) {
    [pscustomobject]@{mcpServers = [pscustomobject]@{sigelc = $entrada}} | ConvertTo-Json -Depth 6
    return
}
$registrado = $config.mcpServers.PSObject.Properties['sigelc']
if ($registrado) {
    if ($registrado.Value.command -ceq $python -and @($registrado.Value.args).Count -eq 1 -and $registrado.Value.args[0] -ceq $servidor) {
        Write-Host 'SIGELC ya está registrado. Reinicie Claude Desktop manualmente.'
        return
    }
    throw 'La entrada sigelc ya existe con otra configuración. Revísela antes de registrar.'
}
if ((Read-Host 'Escriba REGISTRAR para agregar SIGELC a Claude Desktop') -cne 'REGISTRAR') { return }
$config.mcpServers | Add-Member NoteProperty sigelc $entrada
$contenido = ConvertTo-Json -InputObject $config -Depth 100
$validado = ConvertFrom-Json -InputObject $contenido
if ($validado.mcpServers.sigelc.command -cne $python) { throw 'No se pudo validar la configuración.' }
if (-not (Test-Path -LiteralPath $carpeta -PathType Container)) {
    throw 'Abra Claude Desktop una vez para que se cree su carpeta de configuración.'
}
if ((Test-Path -LiteralPath $archivo) -ne $existe) { throw 'La configuración cambió. Repita la verificación.' }
if ($existe) {
    if ((Get-Content -Raw -Encoding UTF8 -LiteralPath $archivo) -cne $previo) {
        throw 'La configuración cambió. Repita la verificación.'
    }
    $respaldo = "$archivo.sigelc-$([guid]::NewGuid().ToString('N')).bak"
    Copy-Item -LiteralPath $archivo -Destination $respaldo
}
$temporal = Join-Path $carpeta "$([guid]::NewGuid().ToString('N')).tmp"
[IO.File]::WriteAllText($temporal, $contenido, [Text.UTF8Encoding]::new($false))
if ($existe) { [IO.File]::Replace($temporal, $archivo, $null) }
else { [IO.File]::Move($temporal, $archivo) }
Write-Host 'SIGELC registrado. Cierre completamente Claude Desktop y vuelva a abrirlo manualmente.'
