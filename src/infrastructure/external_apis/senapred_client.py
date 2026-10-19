"""Alertas vigentes de SENAPRED (senapred.cl/alertas).

El sitio público lee sus alertas desde un servicio GraphQL (AWS AppSync) con
credenciales de **invitado anónimo** de Cognito, el mismo mecanismo que usa
cualquier navegador que abre la página. Aquí se replica ese acceso de sólo
lectura, con pocas consultas (caché de 10 minutos) y enlazando siempre a la
fuente.

.. warning::
   No es una API documentada por SENAPRED: si cambian su sitio, esta
   integración deja de funcionar y la app sigue sin alertas oficiales (no
   inventa ninguna). Lo correcto a mediano plazo es pedir acceso formal.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import logging
import re
import time
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx

from src.domain.value_objects.official_alert import OfficialAlert, OfficialLevel
from src.infrastructure.exceptions import ExternalServiceError

logger = logging.getLogger(__name__)

IDENTITY_POOL = "us-east-1:17c696bc-53e1-49a2-991f-f1b65f752fda"
ENDPOINT = "https://rz2uv7ifxbgflh2bqmp6kmh4le.appsync-api.us-east-1.amazonaws.com/graphql"
AWS_REGION = "us-east-1"
ALERT_URL = "https://senapred.cl/alerta/{slug}"

ROMAN_TO_ISO: Mapping[str, str] = {
    "I": "CL-TA", "II": "CL-AN", "III": "CL-AT", "IV": "CL-CO", "V": "CL-VS", "VI": "CL-LI",
    "VII": "CL-ML", "VIII": "CL-BI", "IX": "CL-AR", "X": "CL-LL", "XI": "CL-AI", "XII": "CL-MA",
    "XIII": "CL-RM", "XIV": "CL-LR", "XV": "CL-AP", "XVI": "CL-NB",
}

_ALERTS_QUERY = """query AlertasByDate($type: String!, $fechaHora: ModelStringKeyConditionInput,
  $sortDirection: ModelSortDirection, $limit: Int, $nextToken: String) {
  alertasByDate(type: $type, fechaHora: $fechaHora, sortDirection: $sortDirection, limit: $limit,
    nextToken: $nextToken) {
    items { id titulo contenido fechaHora isActive isDeleted urlAccess regionesIds variableRiesgo { nombre } }
    nextToken
  }
}"""
_REGIONS_QUERY = "query R($type: String!, $limit: Int) { regionesByCodigo(type: $type, limit: $limit) { items { id codigo } } }"


# ---------------------------------------------------------------- interpretación (funciones puras)


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold()) if not unicodedata.combining(c))


_CANCELLED_CLAUSE = re.compile(r"(?:se )?cancela (?:la |el )?(?:evento de )?alerta (?:roja|amarilla|temprana preventiva)")


def parse_level(title: str) -> OfficialLevel | None:
    """Nivel *declarado*: en «Se cancela Alerta Roja y declara Alerta Amarilla» manda la Amarilla."""
    t = _CANCELLED_CLAUSE.sub(" ", fold(title))
    if "alerta roja" in t:
        return OfficialLevel.ROJA
    if "alerta amarilla" in t:
        return OfficialLevel.AMARILLA
    if "temprana preventiva" in t:
        return OfficialLevel.TEMPRANA_PREVENTIVA
    return None


_COMUNAS = re.compile(r"comunas? de (.+?)(?: por | ante | debido |$)", re.IGNORECASE)
_SCOPE = re.compile(r" para (?:la |las |el |los )?(.+?)(?: por | ante | debido |$)", re.IGNORECASE)
_HAZARD = re.compile(r" (?:por|ante) (.+)$", re.IGNORECASE)


def parse_comunas(title: str) -> tuple[str, ...]:
    """Comunas nombradas en el título («para las Comunas de A, B y C por …»)."""
    match = _COMUNAS.search(title)
    if not match:
        return ()
    names = re.split(r",\s*|\s+y\s+", match.group(1))
    return tuple(n.strip(" .") for n in names if n.strip(" ."))


def parse_scope(title: str) -> str:
    match = _SCOPE.search(title)
    return match.group(1).strip() if match else ""


def parse_hazard(title: str, variable: str | None) -> str:
    if variable and fold(variable) != "otros":
        return variable
    match = _HAZARD.search(title)
    return match.group(1).strip().capitalize() if match else "Emergencia"


def summarize(content: str, limit: int = 320) -> str:
    """Texto plano: separa celdas y párrafos con espacios y corta en ``limit`` caracteres."""
    text = re.sub(r"<(?:/p|/td|br\s*/?)>", " · ", content or "", flags=re.IGNORECASE)
    text = html.unescape(re.sub(r"<[^>]+>", "", text))
    text = re.sub(r"\s*·\s*(·\s*)+", " · ", re.sub(r"\s+", " ", text)).strip(" ·")
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "…"


def to_alerts(items: Sequence[Mapping[str, Any]], region_codes: Mapping[str, str]) -> list[OfficialAlert]:
    """Agrupa las publicaciones por alerta (``urlAccess``) y deja la más reciente si sigue activa."""
    latest: dict[str, Mapping[str, Any]] = {}
    for item in items:
        key = str(item.get("urlAccess") or item.get("id"))
        if key not in latest or str(item.get("fechaHora")) > str(latest[key].get("fechaHora")):
            latest[key] = item
    alerts: list[OfficialAlert] = []
    for key, item in latest.items():
        title = re.sub(r"\s+", " ", str(item.get("titulo") or "")).strip()
        level = parse_level(title)
        # SENAPRED marca la cancelación con isActive=false; «Se cancela X y declara Y» sigue vigente (nivel Y).
        cancelled = fold(title).startswith("se cancela") and "declara" not in fold(title)
        if not item.get("isActive") or item.get("isDeleted") or level is None or cancelled:
            continue
        regions = tuple(dict.fromkeys(region_codes[r] for r in item.get("regionesIds") or [] if r in region_codes))
        if not regions:
            continue
        alerts.append(
            OfficialAlert(
                id=str(item["id"]),
                title=title[:400],
                level=level,
                hazard=parse_hazard(title, (item.get("variableRiesgo") or {}).get("nombre")),
                region_codes=regions,
                comunas=parse_comunas(title),
                scope=parse_scope(title),
                summary=summarize(str(item.get("contenido") or "")),
                published_at=datetime.fromisoformat(str(item["fechaHora"])),
                url=ALERT_URL.format(slug=key),
            )
        )
    return _drop_superseded(alerts)


def _same_scope(a: OfficialAlert, b: OfficialAlert) -> bool:
    if set(a.region_codes) != set(b.region_codes):
        return False
    return {fold(c) for c in a.comunas} == {fold(c) for c in b.comunas}


def _drop_superseded(alerts: list[OfficialAlert]) -> list[OfficialAlert]:
    """Si una alerta más nueva cubre el mismo lugar y amenaza, la anterior quedó reemplazada.

    Ej.: «Se declara Alerta Roja y cancela la Alerta Amarilla para las comunas de ...».
    """
    newest_first = sorted(alerts, key=lambda a: a.published_at, reverse=True)
    kept: list[OfficialAlert] = []
    for alert in newest_first:
        if any(_same_scope(alert, k) and fold(alert.hazard) == fold(k.hazard) for k in kept):
            continue
        kept.append(alert)
    return sorted(kept, key=lambda a: (-a.level.severity, -a.published_at.timestamp()))


# ---------------------------------------------------------------- cliente


class SenapredClient:
    """Lee las alertas vigentes de SENAPRED con credenciales de invitado y caché."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        cache_s: float = 120.0,
        days: int = 21,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._http = http
        self._cache_s = cache_s
        self._days = days
        self._clock = clock
        self._creds: Mapping[str, Any] | None = None
        self._regions: dict[str, str] = {}
        self._cached: tuple[float, list[OfficialAlert]] | None = None

    async def active_alerts(self) -> Sequence[OfficialAlert]:
        """Todas las alertas vigentes del país (lista vacía si SENAPRED no responde)."""
        if self._cached and self._clock() - self._cached[0] < self._cache_s:
            return self._cached[1]
        try:
            if not self._regions:
                data = await self._graphql(_REGIONS_QUERY, {"type": "Region", "limit": 50})
                self._regions = {
                    r["id"]: ROMAN_TO_ISO[r["codigo"]]
                    for r in data["regionesByCodigo"]["items"]
                    if r.get("codigo") in ROMAN_TO_ISO
                }
            items: list[Mapping[str, Any]] = []
            token: str | None = None
            today = date.today()
            for _ in range(5):
                data = await self._graphql(
                    _ALERTS_QUERY,
                    {
                        "type": "Alerta",
                        "sortDirection": "DESC",
                        "limit": 200,
                        "nextToken": token,
                        "fechaHora": {"between": [str(today - timedelta(days=self._days)), f"{today}T23:59:59"]},
                    },
                )
                page = data["alertasByDate"]
                items.extend(page["items"])
                token = page.get("nextToken")
                if not token:
                    break
            alerts = to_alerts(items, self._regions)
        except (httpx.HTTPError, ExternalServiceError, KeyError, TypeError, ValueError) as exc:
            logger.warning("SENAPRED no disponible: %s", exc)
            if self._cached:
                return self._cached[1]  # mejor la última lista conocida que ninguna
            return []
        self._cached = (self._clock(), alerts)
        return alerts

    # ------------------------------------------------------------ AWS

    async def _cognito(self, target: str, body: Mapping[str, Any]) -> Mapping[str, Any]:
        response = await self._http.post(
            f"https://cognito-identity.{AWS_REGION}.amazonaws.com/",
            headers={"X-Amz-Target": f"AWSCognitoIdentityService.{target}", "Content-Type": "application/x-amz-json-1.1"},
            content=json.dumps(body),
        )
        if response.status_code != 200:
            raise ExternalServiceError(f"Cognito respondió {response.status_code}")
        result: Mapping[str, Any] = response.json()
        return result

    async def _credentials(self) -> Mapping[str, Any]:
        expires = float(self._creds["Expiration"]) if self._creds else 0.0
        if self._creds is None or time.time() > expires - 300:
            identity = (await self._cognito("GetId", {"IdentityPoolId": IDENTITY_POOL}))["IdentityId"]
            self._creds = (await self._cognito("GetCredentialsForIdentity", {"IdentityId": identity}))["Credentials"]
        return self._creds

    async def _graphql(self, query: str, variables: Mapping[str, Any]) -> Mapping[str, Any]:
        body = json.dumps({"query": query, "variables": variables}).encode()
        headers = sign_appsync(await self._credentials(), body, datetime.now(UTC))
        response = await self._http.post(ENDPOINT, headers=headers, content=body)
        if response.status_code != 200:
            raise ExternalServiceError(f"SENAPRED respondió {response.status_code}")
        payload: Mapping[str, Any] = response.json()
        if payload.get("errors"):
            raise ExternalServiceError(f"SENAPRED: {payload['errors'][0].get('message', 'error')}")
        data: Mapping[str, Any] = payload["data"]
        return data


def sign_appsync(creds: Mapping[str, Any], body: bytes, now: datetime) -> dict[str, str]:
    """Firma AWS Signature V4 para un POST a AppSync."""
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    day = now.strftime("%Y%m%d")
    headers = {
        "content-type": "application/json",
        "host": httpx.URL(ENDPOINT).host,
        "x-amz-date": amz_date,
        "x-amz-security-token": str(creds["SessionToken"]),
    }
    signed = ";".join(sorted(headers))
    canonical = "\n".join(
        ["POST", "/graphql", "", *(f"{k}:{headers[k]}" for k in sorted(headers)), "", signed, hashlib.sha256(body).hexdigest()]
    )
    scope = f"{day}/{AWS_REGION}/appsync/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])
    key = ("AWS4" + str(creds["SecretKey"])).encode()
    for part in (day, AWS_REGION, "appsync", "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, to_sign.encode(), hashlib.sha256).hexdigest()
    headers["authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={creds['AccessKeyId']}/{scope}, SignedHeaders={signed}, Signature={signature}"
    )
    return headers
