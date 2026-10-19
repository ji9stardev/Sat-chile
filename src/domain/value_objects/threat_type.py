"""Value Objects que tipifican amenazas y fuentes meteorológicas."""

from enum import StrEnum


class ThreatType(StrEnum):
    """Tipos de amenaza natural relevantes para la Región del Maule."""

    INUNDACION = "INUNDACION"
    DESBORDE_RIO = "DESBORDE_RIO"
    REMOCION_EN_MASA = "REMOCION_EN_MASA"
    TORMENTA_ELECTRICA = "TORMENTA_ELECTRICA"
    NEVADA = "NEVADA"
    VIENTO_FUERTE = "VIENTO_FUERTE"
    INCENDIO_FORESTAL = "INCENDIO_FORESTAL"
    SISMO = "SISMO"
    TSUNAMI = "TSUNAMI"

    @property
    def is_hydrometeorological(self) -> bool:
        """``True`` si la amenaza depende de precipitación/escorrentía."""
        return self in _HYDRO_THREATS


_HYDRO_THREATS: frozenset[ThreatType] = frozenset(
    {ThreatType.INUNDACION, ThreatType.DESBORDE_RIO, ThreatType.REMOCION_EN_MASA}
)


class WeatherModel(StrEnum):
    """Modelos globales de Open-Meteo usados en el consenso.

    Los valores coinciden con el parámetro ``models`` de la API de Open-Meteo.
    """

    ECMWF_IFS025 = "ecmwf_ifs025"
    ICON_SEAMLESS = "icon_seamless"
    GFS_SEAMLESS = "gfs_seamless"
