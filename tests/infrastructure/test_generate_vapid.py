"""Las claves generadas deben ser aceptadas por py_vapid (la librería que usa pywebpush)."""

import base64

from py_vapid import Vapid01, b64urlencode
from cryptography.hazmat.primitives import serialization

from src.infrastructure.notifications.generate_vapid import generate_vapid_keys


def test_generated_keys_are_a_valid_pair_for_pywebpush() -> None:
    private, public = generate_vapid_keys()
    vapid = Vapid01.from_string(private)
    derived = vapid.public_key.public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    assert b64urlencode(derived) == public
    assert len(base64.urlsafe_b64decode(public + "==")) == 65  # punto P-256 sin comprimir
    assert "=" not in private and "=" not in public
