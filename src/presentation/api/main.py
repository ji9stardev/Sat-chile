"""Aplicación FastAPI de SAT-Maule (API REST + PWA en el mismo origen).

Ejecutar en desarrollo::

    uvicorn src.presentation.api.main:app --reload

* PWA: ``/`` (archivos de ``src/presentation/pwa``)
* API: ``/api/...`` — documentación interactiva en ``/docs`` y ``/redoc``.

Servir PWA y API desde el mismo origen evita CORS y permite que el Service
Worker controle todo el sitio (``/sw.js`` con alcance ``/``).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from src.domain.exceptions import (
    CapacityExceededError,
    DomainError,
    ExternalDataUnavailableError,
    InvalidStateTransitionError,
    RateLimitedError,
)
from src.infrastructure.config import Settings, get_settings
from src.presentation.api.container import Container, build_container
from src.presentation.api.routers import ALL_ROUTERS

logger = logging.getLogger(__name__)

ContainerFactory = Callable[[Settings], Awaitable[Container]]

PWA_DIR = Path(__file__).resolve().parent.parent / "pwa"

_STATUS_BY_ERROR: tuple[tuple[type[DomainError], int], ...] = (
    (RateLimitedError, status.HTTP_429_TOO_MANY_REQUESTS),
    (InvalidStateTransitionError, status.HTTP_409_CONFLICT),
    (CapacityExceededError, status.HTTP_409_CONFLICT),
    (ExternalDataUnavailableError, status.HTTP_503_SERVICE_UNAVAILABLE),
)
UNPROCESSABLE = 422

CONTENT_SECURITY_POLICY = "; ".join(
    (
        "default-src 'self'",
        "script-src 'self'",
        # Leaflet posiciona capas con estilos en línea.
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: https://tile.openstreetmap.org",
        # El Service Worker hereda esta política: necesita fetch() a las teselas para cachearlas.
        "connect-src 'self' https://tile.openstreetmap.org",
        "frame-src https://embed.windy.com",
        "worker-src 'self'",
        "manifest-src 'self'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    )
)
_NO_CSP_PREFIXES = ("/docs", "/redoc", "/openapi.json")  # Swagger UI carga recursos de un CDN


async def _monitor_loop(container: Container, interval_min: int) -> None:
    """Ejecuta el monitor de zonas cada ``interval_min`` minutos (primera pasada a los 15 s)."""
    await asyncio.sleep(15)
    while True:
        try:
            await container.monitor_zones.execute()
        except Exception:  # noqa: BLE001 - el bucle no debe morir por una pasada fallida
            logger.exception("Falló una pasada del monitor de zonas")
        await asyncio.sleep(interval_min * 60)


def create_app(settings: Settings | None = None, container_factory: ContainerFactory = build_container) -> FastAPI:
    """Fábrica de la aplicación (permite inyectar un contenedor falso en pruebas)."""
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        container = await container_factory(settings)
        app.state.container = container
        monitor: asyncio.Task[None] | None = None
        if settings.monitor_interval_min > 0:
            monitor = asyncio.create_task(_monitor_loop(container, settings.monitor_interval_min))
        logger.info("SAT-Chile iniciado (monitor cada %s min)", settings.monitor_interval_min or "—")
        try:
            yield
        finally:
            if monitor is not None:
                monitor.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await monitor
            await container.close()
            logger.info("SAT-Chile detenido")

    app = FastAPI(
        title="SAT-Chile API",
        version="1.0.0",
        description=(
            "Sistema de Alerta Temprana para la Región del Maule: diagnóstico de riesgo por GPS, "
            "reportes ciudadanos corroborados y notificaciones Web Push."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
        max_age=600,
    )
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("Permissions-Policy", "geolocation=(self), camera=(), microphone=()")
        if not request.url.path.startswith(_NO_CSP_PREFIXES):
            response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        return response

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        code = next((c for t, c in _STATUS_BY_ERROR if isinstance(exc, t)), UNPROCESSABLE)
        headers = {"Retry-After": "600"} if isinstance(exc, RateLimitedError) else None
        return JSONResponse(
            status_code=code, content={"error": type(exc).__name__, "detail": str(exc)}, headers=headers
        )

    @app.exception_handler(ValidationError)
    async def validation_error_handler(request: Request, exc: ValidationError) -> JSONResponse:
        # Errores de invariantes de dominio al construir entidades/VOs con datos del cliente.
        return JSONResponse(
            status_code=UNPROCESSABLE,
            content={"error": "ValidationError", "detail": exc.errors(include_url=False, include_context=False)},
        )

    for router in ALL_ROUTERS:
        app.include_router(router)

    if PWA_DIR.is_dir():
        _mount_pwa(app)
    return app


def _mount_pwa(app: FastAPI) -> None:
    """Sirve la PWA. ``sw.js`` e ``index.html`` sin caché HTTP para que las actualizaciones lleguen."""

    @app.get("/sw.js", include_in_schema=False)
    async def service_worker() -> FileResponse:
        return FileResponse(
            PWA_DIR / "sw.js",
            media_type="text/javascript",
            headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"},
        )

    @app.get("/manifest.json", include_in_schema=False)
    async def manifest() -> FileResponse:
        return FileResponse(PWA_DIR / "manifest.json", media_type="application/manifest+json")

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(PWA_DIR / "index.html", headers={"Cache-Control": "no-cache"})

    app.mount("/", StaticFiles(directory=PWA_DIR), name="pwa")


app = create_app()
