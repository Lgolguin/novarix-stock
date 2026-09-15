"""Generate only the NOVARIX signature key; never Mercado Pago credentials."""
import base64
from pathlib import Path
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def main():
    path = Path(__file__).with_name('.env')
    contents = path.read_text(encoding='utf-8') if path.exists() else ''
    existing = [line for line in contents.splitlines() if line.startswith('STOCK_LICENSE_PRIVATE_KEY=') and line.split('=', 1)[1].strip()]
    if existing:
        raise SystemExit('Ya existe una clave privada. No se reemplazó; rotar claves invalidaría las licencias guardadas.')
    key = Ed25519PrivateKey.generate()
    private = base64.b64encode(key.private_bytes(serialization.Encoding.Raw,
                              serialization.PrivateFormat.Raw, serialization.NoEncryption())).decode()
    public = base64.b64encode(key.public_key().public_bytes(serialization.Encoding.Raw,
                             serialization.PublicFormat.Raw)).decode()
    lines = [line for line in contents.splitlines() if not line.startswith('STOCK_LICENSE_PRIVATE_KEY=')]
    path.write_text('\n'.join(lines) + '\nSTOCK_LICENSE_PRIVATE_KEY=' + private + '\n', encoding='utf-8')
    print('Clave privada guardada únicamente en backend/.env. No compartir ese archivo.')
    print('Configuración pública para el cliente:')
    print('STOCK_LICENSE_PUBLIC_KEY=' + public)


if __name__ == '__main__':
    main()
