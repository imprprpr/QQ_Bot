"""Bounded JSON requests with credential-free phase metrics and transient retry."""
import asyncio
import json
import math
import random
import time
from email.utils import parsedate_to_datetime
from types import SimpleNamespace
from urllib.parse import urlsplit
import uuid

import aiohttp
from nonebot import logger

from .runtime import record


class HttpFailure(ValueError):
    def __init__(self, status):
        super().__init__(f"HTTP_{status}")
        self.status = status


def phase(metrics):
    if "headers_ms" in metrics:
        return "response_body"
    if "sent_ms" in metrics:
        # Includes upstream queue/compute AND the return network path.
        return "waiting_upstream_or_return_path"
    if "auth_ms" not in metrics:
        return "authentication"
    return "dns_connect_tls_or_upload"


def trace_config():
    trace = aiohttp.TraceConfig(trace_config_ctx_factory=lambda *, trace_request_ctx=None:
                                SimpleNamespace(metrics=trace_request_ctx))

    def mark(name):
        async def callback(session, context, params):
            metrics = context.metrics
            metrics[name] = round((time.monotonic() - metrics["_started"]) * 1000, 2)
        return callback

    for signal, name in [(trace.on_dns_resolvehost_start, "dns_start_ms"),
                         (trace.on_dns_resolvehost_end, "dns_end_ms"),
                         (trace.on_connection_create_start, "connect_start_ms"),
                         (trace.on_connection_create_end, "connect_end_ms"),
                         (trace.on_connection_reuseconn, "connection_reused_ms"),
                         (trace.on_request_headers_sent, "request_headers_ms"),
                         (trace.on_request_chunk_sent, "sent_ms")]:
        signal.append(mark(name))
    return trace


async def emit(metrics, status, observer=None):
    detail = {key: value for key, value in metrics.items() if not key.startswith("_")}
    detail["elapsed_ms"] = round((time.monotonic() - metrics["_started"]) * 1000, 2)
    logger.info("HTTP timing: {}", detail)
    try:
        await record("model-http", status, **detail)
        if observer:
            await observer(detail, status)
    except Exception as exc:
        logger.warning("HTTP timing write failed: {}", type(exc).__name__)


async def request_json(url, payload, headers_factory, *, timeout=89, attempt_timeout=35,
                       max_attempts=2, proxy=None, label="model", max_bytes=8 * 1024 * 1024,
                       observer=None):
    if not math.isfinite(timeout) or not math.isfinite(attempt_timeout) or timeout <= 0 or attempt_timeout <= 0 or not 1 <= max_attempts <= 3:
        raise ValueError("INVALID_HTTP_RETRY_CONFIG")
    deadline = time.monotonic() + timeout
    identity = uuid.uuid4().hex[:12]
    force_auth = False
    async with aiohttp.ClientSession(trace_configs=[trace_config()]) as session:
        for attempt in range(1, max_attempts + 1):
            metrics = {"_started": time.monotonic(), "request_id": identity, "label": label,
                       "host": urlsplit(url).hostname, "attempt": attempt}
            retry_after = None
            try:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise asyncio.TimeoutError()
                headers = await asyncio.wait_for(headers_factory(force_auth), timeout=min(12, remaining))
                metrics["auth_ms"] = round((time.monotonic() - metrics["_started"]) * 1000, 2)
                budget = deadline - time.monotonic()
                if attempt < max_attempts or max_attempts == 1:
                    budget = min(attempt_timeout, budget)
                if budget <= 0:
                    raise asyncio.TimeoutError()
                limits = aiohttp.ClientTimeout(total=budget, connect=min(8, budget), sock_connect=min(8, budget))
                async with session.post(url, json=payload, headers=headers, proxy=proxy,
                                        timeout=limits, trace_request_ctx=metrics) as response:
                    metrics["headers_ms"] = round((time.monotonic() - metrics["_started"]) * 1000, 2)
                    metrics["http_status"] = response.status
                    if "sent_ms" in metrics:
                        metrics["wait_headers_ms"] = round(metrics["headers_ms"] - metrics["sent_ms"], 2)
                    if response.status != 200:
                        value = response.headers.get("Retry-After", "")
                        try:
                            retry_after = max(0, float(value))
                        except ValueError:
                            try:
                                retry_after = max(0, parsedate_to_datetime(value).timestamp() - time.time())
                            except (ValueError, TypeError, OverflowError):
                                pass
                        raise HttpFailure(response.status)
                    data = bytearray()
                    async for chunk in response.content.iter_chunked(65536):
                        if "first_body_ms" not in metrics:
                            metrics["first_body_ms"] = round((time.monotonic() - metrics["_started"]) * 1000, 2)
                        data.extend(chunk)
                        if len(data) > max_bytes:
                            raise ValueError("MODEL_RESPONSE_TOO_LARGE")
                    result = json.loads(data)
                    metrics["bytes"] = len(data)
                    await emit(metrics, "completed", observer)
                    return result
            except asyncio.CancelledError:
                metrics.update(error="CancelledError", phase=phase(metrics))
                await emit(metrics, "cancelled", observer)
                raise
            except Exception as exc:
                metrics.update(error=type(exc).__name__, phase=phase(metrics))
                if isinstance(exc, HttpFailure):
                    metrics["phase"] = "http_status"
                    retry = exc.status in {408, 429, 500, 502, 503, 504}
                    if exc.status == 401 and not force_auth:
                        force_auth = True
                        retry = True
                else:
                    retry = isinstance(exc, (asyncio.TimeoutError, aiohttp.ClientConnectionError,
                                             aiohttp.ClientPayloadError))
                    if isinstance(exc, (aiohttp.ClientSSLError, aiohttp.ClientConnectorCertificateError)):
                        retry = False
                delay = retry_after if retry_after is not None else random.uniform(.5, 1.2) * (2 ** (attempt - 1))
                remaining = deadline - time.monotonic()
                retry = retry and attempt < max_attempts and delay + .2 < remaining
                if retry:
                    metrics["retry_delay_ms"] = round(delay * 1000, 2)
                await emit(metrics, "retrying" if retry else "failed", observer)
                if not retry:
                    raise
                await asyncio.sleep(delay)
    raise RuntimeError("UNREACHABLE_HTTP_RETRY")
