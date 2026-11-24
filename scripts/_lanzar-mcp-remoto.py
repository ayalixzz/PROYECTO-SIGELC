"""Launch one pilot per port and publish a private shutdown marker."""
import json
import msvcrt
from pathlib import Path
import runpy
import sys
from uuid import uuid4


def main():
    marcador = Path(sys.argv[1])
    servidor = Path(sys.argv[2])
    argumentos = sys.argv[3:]
    with marcador.with_suffix('.lock').open('a+b') as lock:
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise SystemExit('Ya se está ejecutando el MCP remoto de SIGELC en ese puerto.') from error
        instancia = uuid4().hex
        senal = marcador.with_name(f'{marcador.stem}-{instancia}.stop')
        temporal = marcador.with_name(f'{marcador.stem}-{instancia}.tmp')
        temporal.write_text(json.dumps({'instancia': instancia}), encoding='utf-8')
        temporal.replace(marcador)
        try:
            sys.path.insert(0, str(servidor.parent))
            sys.argv = [str(servidor), *argumentos]
            runpy.run_path(str(servidor))['main'](senal_detener=senal)
        finally:
            marcador.unlink(missing_ok=True)
            senal.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
