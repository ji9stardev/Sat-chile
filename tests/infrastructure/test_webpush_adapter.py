"""Pruebas del adaptador Web Push con un *sender* falso (sin red)."""

from typing import Any

from pywebpush import WebPushException
from requests import Response

from src.domain.repositories import NotificationGateway
from src.domain.value_objects import DeliveryStatus, PushNotification, PushSubscription, RiskLevel
from src.infrastructure.notifications import WebPushAdapter

SUB_OK = PushSubscription(endpoint="https://push.example/ok", p256dh="B" * 87, auth="a" * 22)
SUB_GONE = PushSubscription(endpoint="https://push.example/gone", p256dh="B" * 87, auth="a" * 22)
SUB_ERR = PushSubscription(endpoint="https://push.example/err", p256dh="B" * 87, auth="a" * 22)
NOTIF = PushNotification(title="Alerta ROJA", body="Evacúe", risk_level=RiskLevel.ROJO, tag="zona-1")


def _response(code: int) -> Response:
    r = Response()
    r.status_code = code
    return r


class FakeSender:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, **kwargs: Any) -> Response:
        self.calls.append(kwargs)
        endpoint = kwargs["subscription_info"]["endpoint"]
        kwargs["vapid_claims"]["aud"] = "mutated"  # imita a pywebpush
        if endpoint.endswith("gone"):
            raise WebPushException("Gone", response=_response(410))
        if endpoint.endswith("err"):
            raise WebPushException("Server error", response=_response(500))
        return _response(201)


async def test_send_many_classifies_results() -> None:
    sender = FakeSender()
    adapter = WebPushAdapter("fake-key", "mailto:ops@example.cl", sender=sender)
    assert isinstance(adapter, NotificationGateway)

    results = await adapter.send_many([SUB_OK, SUB_GONE, SUB_ERR], NOTIF)

    assert [r.status for r in results] == [
        DeliveryStatus.ENTREGADA,
        DeliveryStatus.SUSCRIPCION_EXPIRADA,
        DeliveryStatus.FALLIDA,
    ]
    assert results[1].status_code == 410
    first = sender.calls[0]
    assert first["headers"] == {"Urgency": "high"}
    assert first["ttl"] == NOTIF.ttl_seconds
    # Cada envío recibe un dict de claims nuevo aunque pywebpush lo mute.
    assert all(c["vapid_claims"]["sub"] == "mailto:ops@example.cl" for c in sender.calls)
    assert len({id(c["vapid_claims"]) for c in sender.calls}) == 3
