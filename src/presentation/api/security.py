"""Utilidades de privacidad para la API."""

from __future__ import annotations

import hashlib
import hmac


def anonymize_device_id(device_id: str, secret: str) -> str:
    """HMAC-SHA256 del identificador del dispositivo.

    Permite contar "vecinos distintos" y limitar abusos sin almacenar el
    identificador real; sin el secreto del servidor no es reversible ni
    correlacionable con otros sistemas.
    """
    return hmac.new(secret.encode(), device_id.encode(), hashlib.sha256).hexdigest()
