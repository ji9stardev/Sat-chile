"""Genera un par de claves VAPID (P-256) listo para pegar en ``.env``.

Uso::

    python -m src.infrastructure.notifications.generate_vapid

* ``SAT_VAPID_PRIVATE_KEY``: escalar privado de 32 bytes en base64url (formato
  "raw" que acepta ``pywebpush``/``py_vapid``). **Secreto**: nunca al repositorio.
* ``SAT_VAPID_PUBLIC_KEY``: punto público sin comprimir (65 bytes) en
  base64url, el ``applicationServerKey`` que usa el navegador.

Si se cambian las claves, todas las suscripciones existentes dejan de
funcionar y los usuarios deben volver a activar la protección.
"""

from __future__ import annotations

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def generate_vapid_keys() -> tuple[str, str]:
    """Devuelve ``(privada_base64url, publica_base64url)``."""
    private_key = ec.generate_private_key(ec.SECP256R1())
    private_raw = private_key.private_numbers().private_value.to_bytes(32, "big")
    public_raw = private_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return _b64url(private_raw), _b64url(public_raw)


def main() -> None:
    private, public = generate_vapid_keys()
    print("# Claves VAPID para SAT-Maule (copiar en .env o en las variables del hosting)")
    print(f"SAT_VAPID_PRIVATE_KEY={private}")
    print(f"SAT_VAPID_PUBLIC_KEY={public}")


if __name__ == "__main__":
    main()
