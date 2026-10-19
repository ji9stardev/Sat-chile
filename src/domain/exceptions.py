"""Excepciones del dominio de SAT-Maule.

Todas las excepciones heredan de :class:`DomainError` para que las capas
externas (API, workers) puedan capturar errores de negocio de forma uniforme
sin depender de detalles internos.
"""


class DomainError(Exception):
    """Error base para cualquier violación de reglas de negocio."""


class InsufficientModelDataError(DomainError):
    """No hay suficientes modelos meteorológicos para calcular un consenso fiable."""


class InvalidStateTransitionError(DomainError):
    """Se intentó una transición de estado no permitida en una entidad."""


class CapacityExceededError(DomainError):
    """Un punto seguro no tiene capacidad para recibir más personas."""


class RateLimitedError(DomainError):
    """El mismo emisor repitió una acción antes del tiempo mínimo permitido."""


class ExternalDataUnavailableError(DomainError):
    """Una fuente externa de datos (clima, elevación) no está disponible.

    Los adaptadores de infraestructura la especializan, de modo que la capa
    de aplicación puede degradarse con elegancia sin conocer detalles de HTTP.
    """
