"""Escribe dos ZIP de ejemplo con datos sintéticos. Funciona sin Django ni PostgreSQL."""
from argparse import ArgumentParser
from pathlib import Path
import sys

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ / 'backend'))

from gestion_contractual.contenido_demo import paquetes_ficticios  # noqa: E402


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--salida', type=Path, default=RAIZ / 'demo-paquetes')
    destino = parser.parse_args().salida
    destino.mkdir(parents=True, exist_ok=True)
    for nombre, contenido in paquetes_ficticios():
        archivo = destino / nombre
        try:
            with archivo.open('xb') as salida:
                salida.write(contenido)
        except FileExistsError:
            if archivo.read_bytes() != contenido:
                parser.exit(1, f'El archivo {archivo} ya existe y contiene datos distintos. Use otra carpeta.\n')
        print(archivo)


if __name__ == '__main__':
    main()
