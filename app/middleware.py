from __future__ import annotations

import os
import re
import time
import uuid

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from structlog.contextvars import bind_contextvars, clear_contextvars

CORRELATION_ID_HEADER = "x-request-id"
RESPONSE_TIME_HEADER = "x-response-time-ms"
GENERATED_PREFIX = "req-"
HEX_PATTERN = re.compile(r"^[0-9a-fA-F]+$")


def _generate_correlation_id() -> str:
    return f"{GENERATED_PREFIX}{uuid.uuid4().hex[:8]}"


def _normalize_correlation_id(raw: str | None) -> str:
    if not raw:
        return _generate_correlation_id()
    value = raw.strip()
    if value.startswith(GENERATED_PREFIX) and len(value) > len(GENERATED_PREFIX):
        suffix = value[len(GENERATED_PREFIX):]
        if len(suffix) >= 8 and HEX_PATTERN.match(suffix[:8]):
            return f"{GENERATED_PREFIX}{suffix[:8].lower()}"
    return _generate_correlation_id()


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        clear_contextvars()

        correlation_id = _normalize_correlation_id(request.headers.get(CORRELATION_ID_HEADER))
        bind_contextvars(correlation_id=correlation_id)

        request.state.correlation_id = correlation_id

        env = os.getenv("APP_ENV", "dev")
        bind_contextvars(env=env)

        start = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = int((time.perf_counter() - start) * 1000)

        response.headers[CORRELATION_ID_HEADER] = correlation_id
        response.headers[RESPONSE_TIME_HEADER] = str(elapsed_ms)

        return response
