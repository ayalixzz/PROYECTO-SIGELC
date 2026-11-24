"""Test OAuth behind HTTPS locally, using two provisioned fictional deliveries."""
import argparse
from datetime import datetime, timedelta, timezone
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import json
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, sleep

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
import ipaddress
import httpx

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--entregas', nargs=2, type=int, default=[1, 2], metavar='ID')
args = parser.parse_args()
if args.entregas[0] == args.entregas[1] or min(args.entregas) < 1:
    parser.error('Indique dos entregas ficticias distintas.')
raiz = Path(__file__).resolve().parents[1]
carpeta = raiz / 'backend' / 'private_media_demo' / '.runtime'
carpeta.mkdir(parents=True, exist_ok=True)
with socket.socket() as reserva:
    reserva.bind(('127.0.0.1', 0))
    interno = reserva.getsockname()[1]


def certificado(ruta):
    llave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nombre = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'SIGELC Proxi TLS de ensayo')])
    ahora = datetime.now(timezone.utc)
    certificado = (x509.CertificateBuilder().subject_name(nombre).issuer_name(nombre)
                   .public_key(llave.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(ahora - timedelta(minutes=5)).not_valid_after(ahora + timedelta(hours=1))
                   .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]),
                                  critical=False)
                   .sign(llave, hashes.SHA256()))
    crt, secret = ruta / 'certificado.pem', ruta / 'llave.pem'
    crt.write_bytes(certificado.public_bytes(serialization.Encoding.PEM))
    secret.write_bytes(llave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    return crt, secret


class Proxy(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *args):
        pass

    def enviar(self):
        destino = HTTPConnection('127.0.0.1', interno, timeout=15)
        try:
            cabeceras = {k: v for k, v in self.headers.items() if k.lower() not in
                         ('connection', 'x-forwarded-proto', 'x-forwarded-host', 'forwarded')}
            cabeceras.update({'X-Forwarded-Proto': 'http', 'X-Forwarded-Host': 'url-invalida.invalid'})
            cuerpo = self.rfile.read(int(self.headers.get('Content-Length', '0')))
            try:
                destino.request(self.command, self.path, body=cuerpo, headers=cabeceras)
                respuesta = destino.getresponse()
            except ConnectionRefusedError:
                self.send_response_only(503)
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            datos = respuesta.read()
            self.send_response_only(respuesta.status)
            for nombre, valor in respuesta.getheaders():
                if nombre.lower() not in ('connection', 'transfer-encoding', 'content-length'):
                    self.send_header(nombre, valor)
            self.send_header('Content-Length', str(len(datos)))
            self.end_headers()
            self.wfile.write(datos)
        finally:
            destino.close()

    do_GET = do_POST = do_DELETE = do_OPTIONS = enviar


with TemporaryDirectory(dir=carpeta) as temporal, ThreadingHTTPServer(('127.0.0.1', 0), Proxy) as proxy:
    crt, llave = certificado(Path(temporal))
    identidad = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    identidad.load_cert_chain(crt, llave)
    proxy.socket = identidad.wrap_socket(proxy.socket, server_side=True)
    Thread(target=proxy.serve_forever, daemon=True).start()
    publica = f'https://127.0.0.1:{proxy.server_port}'
    entorno = {k: v for k, v in os.environ.items() if k != 'SIGELC_MCP_PRUEBA_PASSWORD'}
    marcador = Path(temporal) / 'mcp-https-ensayo.json'
    with (Path(temporal) / 'servidor-https.log').open('w', encoding='utf-8') as log:
        proceso = subprocess.Popen(
            [sys.executable, str(raiz / 'scripts/_lanzar-mcp-remoto.py'), str(marcador),
             str(raiz / 'backend/sigelc_mcp_remoto.py'), '--url-publica', publica,
             '--puerto', str(interno), '--entrega', str(args.entregas[0]), '--entrega', str(args.entregas[1]),
             '--ttl-acceso', '20'],
            stdout=log, stderr=log, env=entorno,
        )
        try:
            fin = monotonic() + 20
            with httpx.Client(verify=ssl.create_default_context(cafile=str(crt)), trust_env=False) as cliente:
                while proceso.poll() is None and monotonic() < fin:
                    try:
                        p = cliente.get(publica + '/.well-known/oauth-protected-resource/mcp')
                        if p.status_code == 200 and p.json()['resource'] == publica + '/mcp':
                            break
                    except (httpx.TransportError, KeyError):
                        pass
                    sleep(0.2)
                else:
                    raise RuntimeError('No se pudo iniciar el servidor MCP por HTTPS local.')
            prueba = subprocess.run(
                [sys.executable, str(raiz / 'scripts/probar-sigelc-mcp-remoto.py'),
                 '--base-url', publica, '--ca', str(crt), '--esperar-expiracion', '--probar-frecuencia'], timeout=90,
            )
            sys.exit(prueba.returncode)
        finally:
            if proceso.poll() is None and marcador.exists():
                instancia = json.loads(marcador.read_text(encoding='utf-8'))['instancia']
                marcador.with_name(f'{marcador.stem}-{instancia}.stop').touch()
            proceso.wait(15)
            proxy.shutdown()
