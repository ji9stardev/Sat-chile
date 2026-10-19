"""Adaptador Web Push (VAPID, RFC 8292) sobre ``pywebpush``.

Web Push es gratuito: el navegador entrega un ``endpoint`` del servicio push
de su fabricante (FCM, Mozilla, Apple) y el servidor firma cada envío con su
par de claves VAPID. Para generarlas una vez::

    vapid --gen            # (paquete py-vapid, dependencia de pywebpush)
    vapid --applicationServerKey   # clave pública para el frontend

``pywebpush`` es síncrono (usa ``requests``), por lo que cada envío se
ejecuta en un hilo con ``asyncio.to_thread`` y la concurrencia se acota con
un semáforo para no saturar el pool de hilos durante un envío masivo.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable, Sequence
from typing import Any

from pywebpush import WebPushException, webpush

from src.domain.entities.alert import Alert
from src.domain.value_objects.notification import (
    DeliveryResult,
    DeliveryStatus,
    PushNotification,
    PushSubscription,
)
from src.domain.value_objects.risk_level import RiskLevel
from src.infrastructure.config import Settings

logger = logging.getLogger(__name__)

EXPIRED_STATUS_CODES = frozenset({404, 410})

_URGENCY: dict[RiskLevel, str] = {
    RiskLevel.ROJO: "high",
    RiskLevel.NARANJA: "high",
    RiskLevel.AMARILLO: "normal",
    RiskLevel.VERDE: "low",
}

WebPushSender = Callable[..., Any]


def notification_from_alert(alert: Alert) -> PushNotification:
    """Construye la notificación de una alerta (el tag por zona reemplaza la anterior)."""
    return PushNotification(
        title=alert.title,
        body=alert.message[:2000],
        risk_level=alert.risk_level,
        tag=f"zona-{alert.zone_id}"[:64],
        url=f"/?alerta={alert.id}",
        ttl_seconds=max(0, min(int((alert.valid_until - alert.valid_from).total_seconds()), 28 * 86_400)),
    )


class WebPushAdapter:
    """Implementación de :class:`NotificationGateway` con VAPID.

    Args:
        vapid_private_key: Clave privada VAPID (PEM o base64url DER).
        vapid_subject: Contacto del emisor (``mailto:`` o URL https).
        max_concurrency: Envíos simultáneos máximos.
        sender: Función de envío (por defecto ``pywebpush.webpush``; inyectable en tests).
        request_timeout_s: Timeout HTTP por envío.
    """

    def __init__(
        self,
        vapid_private_key: str,
        vapid_subject: str,
        *,
        max_concurrency: int = 20,
        sender: WebPushSender = webpush,
        request_timeout_s: float = 10.0,
    ) -> None:
        if not vapid_subject.startswith(("mailto:", "https://")):
            raise ValueError("vapid_subject debe comenzar con 'mailto:' o 'https://'.")
        self._private_key = vapid_private_key
        self._subject = vapid_subject
        self._semaphore = asyncio.Semaphore(max_concurrency)
        self._sender = sender
        self._timeout = request_timeout_s

    @classmethod
    def from_settings(cls, settings: Settings) -> WebPushAdapter:
        """Construye el adaptador; exige ``SAT_VAPID_PRIVATE_KEY``."""
        if settings.vapid_private_key is None:
            raise ValueError("Falta SAT_VAPID_PRIVATE_KEY para enviar notificaciones push.")
        return cls(
            settings.vapid_private_key.get_secret_value(),
            settings.vapid_subject,
            max_concurrency=settings.push_max_concurrency,
        )

    async def send(self, subscription: PushSubscription, notification: PushNotification) -> DeliveryResult:
        """Envía una notificación. Nunca lanza por fallos de entrega."""
        endpoint = str(subscription.endpoint)
        payload = json.dumps(
            {
                "title": notification.title,
                "body": notification.body,
                "level": notification.risk_level.value,
                "tag": notification.tag,
                "url": notification.url,
            },
            ensure_ascii=False,
        )
        async with self._semaphore:
            try:
                response = await asyncio.to_thread(
                    self._sender,
                    subscription_info=subscription.to_webpush_info(),
                    data=payload,
                    vapid_private_key=self._private_key,
                    # pywebpush muta este dict (agrega aud/exp): uno nuevo por envío.
                    vapid_claims={"sub": self._subject},
                    ttl=notification.ttl_seconds,
                    headers={"Urgency": _URGENCY[notification.risk_level]},
                    timeout=self._timeout,
                )
            except WebPushException as exc:
                code = exc.response.status_code if exc.response is not None else None
                status = (
                    DeliveryStatus.SUSCRIPCION_EXPIRADA
                    if code in EXPIRED_STATUS_CODES
                    else DeliveryStatus.FALLIDA
                )
                if status is DeliveryStatus.FALLIDA:
                    logger.warning("Fallo Web Push (%s) a %s: %s", code, _short(endpoint), exc)
                return DeliveryResult(endpoint=endpoint, status=status, status_code=code, detail=str(exc)[:500])
            except Exception as exc:  # noqa: BLE001 - errores de red no deben cortar un envío masivo
                logger.warning("Error de red Web Push a %s: %s", _short(endpoint), exc)
                return DeliveryResult(endpoint=endpoint, status=DeliveryStatus.FALLIDA, detail=str(exc)[:500])

        code = getattr(response, "status_code", None)
        return DeliveryResult(endpoint=endpoint, status=DeliveryStatus.ENTREGADA, status_code=code)

    async def send_many(
        self, subscriptions: Sequence[PushSubscription], notification: PushNotification
    ) -> Sequence[DeliveryResult]:
        """Envío concurrente acotado por el semáforo; preserva el orden de entrada."""
        return await asyncio.gather(*(self.send(s, notification) for s in subscriptions))


def _short(endpoint: str) -> str:
    """Endpoint truncado para logs (contiene un identificador del dispositivo)."""
    return endpoint[:48] + "…"
