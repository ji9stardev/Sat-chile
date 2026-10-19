"""Gateway de reemplazo cuando no hay claves VAPID configuradas (desarrollo)."""

from __future__ import annotations

import logging
from collections.abc import Sequence

from src.domain.value_objects.notification import (
    DeliveryResult,
    DeliveryStatus,
    PushNotification,
    PushSubscription,
)

logger = logging.getLogger(__name__)


class DisabledNotificationGateway:
    """No envía nada: registra en el log y responde FALLIDA, para que nadie crea que se notificó."""

    async def send(self, subscription: PushSubscription, notification: PushNotification) -> DeliveryResult:
        logger.warning("Web Push deshabilitado (sin SAT_VAPID_PRIVATE_KEY): '%s' no enviado", notification.title)
        return DeliveryResult(
            endpoint=str(subscription.endpoint),
            status=DeliveryStatus.FALLIDA,
            detail="Web Push deshabilitado: falta SAT_VAPID_PRIVATE_KEY",
        )

    async def send_many(
        self, subscriptions: Sequence[PushSubscription], notification: PushNotification
    ) -> Sequence[DeliveryResult]:
        return [await self.send(s, notification) for s in subscriptions]
