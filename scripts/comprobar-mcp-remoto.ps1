[CmdletBinding()]
param(
    [ValidateRange(1024, 65535)]
    [int]$Puerto = 8765,
    [uri]$UrlPublica,
    [switch]$Publico
)

$ErrorActionPreference = 'Stop'
if (-not $UrlPublica) { $UrlPublica = [uri]"http://127.0.0.1:$Puerto" }
if (-not $UrlPublica.IsAbsoluteUri -or $UrlPublica.AbsolutePath -ne '/' -or
    $UrlPublica.UserInfo -or $UrlPublica.Query -or $UrlPublica.Fragment -or
    ($UrlPublica.Scheme -ne 'https' -and $UrlPublica.AbsoluteUri -ne "http://127.0.0.1:$Puerto/") -or
    ($Publico -and $UrlPublica.Scheme -ne 'https')) {
    throw 'Indique el origen HTTPS, sin /mcp, o use el loopback HTTP con el puerto local.'
}
$origen = $UrlPublica.GetLeftPart([UriPartial]::Authority)
$destino = if ($Publico) { $origen } else { "http://127.0.0.1:$Puerto" }
Add-Type -AssemblyName System.Net.Http
$transporte = [Net.Http.HttpClientHandler]::new()
$transporte.AllowAutoRedirect = $false
$transporte.UseProxy = $false
$cliente = [Net.Http.HttpClient]::new($transporte)
$cliente.Timeout = [TimeSpan]::FromSeconds(10)
$cliente.MaxResponseContentBufferSize = 98304

function Probar-Ruta([string]$Ruta, [int]$Esperado, [string]$Cuerpo) {
    $metodo = if ($Cuerpo) { [Net.Http.HttpMethod]::Post } else { [Net.Http.HttpMethod]::Get }
    $peticion = [Net.Http.HttpRequestMessage]::new($metodo, "$destino$Ruta")
    try {
        $peticion.Headers.Host = $UrlPublica.Authority
        if ($Cuerpo) {
            $peticion.Headers.Add('Accept', 'application/json, text/event-stream')
            $peticion.Content = [Net.Http.StringContent]::new($Cuerpo, [Text.Encoding]::UTF8, 'application/json')
        }
        $respuesta = $cliente.SendAsync($peticion).GetAwaiter().GetResult()
        try {
            if ([int]$respuesta.StatusCode -ne $Esperado) {
                throw "$Ruta devolvió HTTP $([int]$respuesta.StatusCode). Se esperaba HTTP $Esperado."
            }
            Write-Host "$Ruta · HTTP $Esperado"
            return @{
                Texto = $respuesta.Content.ReadAsStringAsync().GetAwaiter().GetResult()
                Desafio = $respuesta.Headers.WwwAuthenticate -join ', '
            }
        } finally { $respuesta.Dispose() }
    } finally { $peticion.Dispose() }
}

try {
    $rutaRecurso = '/.well-known/oauth-protected-resource/mcp'
    $recurso = (Probar-Ruta $rutaRecurso 200).Texto | ConvertFrom-Json
    $emisores = @($recurso.authorization_servers | ForEach-Object { $_.TrimEnd('/') })
    if ($recurso.resource -cne "$origen/mcp" -or $origen -cnotin $emisores) {
        throw 'El recurso OAuth y el servidor de autorización deben anunciar el origen configurado.'
    }
    $oauth = (Probar-Ruta '/.well-known/oauth-authorization-server' 200).Texto | ConvertFrom-Json
    if ($oauth.issuer.TrimEnd('/') -cne $origen -or 'S256' -cnotin $oauth.code_challenge_methods_supported -or
        $oauth.authorization_endpoint -cne "$origen/authorize" -or $oauth.token_endpoint -cne "$origen/token" -or
        $oauth.registration_endpoint -cne "$origen/register" -or $oauth.revocation_endpoint -cne "$origen/revoke") {
        throw 'Los metadatos OAuth no anuncian PKCE S256 y los endpoints previstos en el origen configurado.'
    }
    $init = '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"sigelc-verificador","version":"1.0"}}}'
    $desafio = (Probar-Ruta '/mcp' 401 $init).Desafio
    if ($desafio -notlike '*Bearer*' -or -not $desafio.Contains("$origen$rutaRecurso")) {
        throw 'Falta el desafío Bearer con la URL de metadatos del recurso protegido.'
    }
    foreach ($ruta in @('/', '/admin/', '/api/', '/media/', '/private_media_demo/', '/.env')) {
        $null = Probar-Ruta $ruta 404
    }
    Write-Host 'Descubrimiento OAuth, protección de /mcp y aislamiento de rutas verificados.'
} finally { $cliente.Dispose() }
