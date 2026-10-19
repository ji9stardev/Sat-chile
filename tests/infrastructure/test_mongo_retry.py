"""Pruebas de la política de reintentos de MongoDB (sin servidor)."""

import pytest
from pymongo.errors import AutoReconnect, ServerSelectionTimeoutError

from src.domain.exceptions import ExternalDataUnavailableError
from src.infrastructure.database.mongo_client import with_retry
from src.infrastructure.exceptions import DatabaseUnavailableError


async def test_server_selection_timeout_is_not_retried_and_is_translated() -> None:
    calls = {"n": 0}

    async def op() -> int:
        calls["n"] += 1
        raise ServerSelectionTimeoutError("sin servidores")

    with pytest.raises(DatabaseUnavailableError) as info:
        await with_retry(op, base_delay_s=0)
    assert calls["n"] == 1
    assert isinstance(info.value, ExternalDataUnavailableError)


async def test_transient_errors_are_retried_then_succeed() -> None:
    calls = {"n": 0}

    async def op() -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise AutoReconnect("elección de primario")
        return "ok"

    assert await with_retry(op, base_delay_s=0) == "ok"
    assert calls["n"] == 3


async def test_exhausted_retries_raise_database_unavailable() -> None:
    async def op() -> None:
        raise AutoReconnect("caído")

    with pytest.raises(DatabaseUnavailableError):
        await with_retry(op, attempts=2, base_delay_s=0)
