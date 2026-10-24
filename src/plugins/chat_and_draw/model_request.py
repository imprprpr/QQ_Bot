"""Bounded SDK retries; private and managed transports already guard themselves."""
import asyncio
import math
import time
from email.utils import parsedate_to_datetime

from openai import AsyncOpenAI, APIConnectionError

from .rate_limit import endpoint_gate, ModelBusyError
from .runtime import record


async def complete(client, timeout, **kwargs):
    if not isinstance(client, AsyncOpenAI):
        return await client.chat.completions.create(**kwargs)
    gate = endpoint_gate(str(client.base_url) + '\0' + client.api_key)
    deadline = time.monotonic() + timeout
    try:
        await asyncio.wait_for(gate.lock.acquire(), min(10, timeout))
    except asyncio.TimeoutError:
        raise ModelBusyError('MODEL_QUEUE_BUSY') from None
    try:
        for attempt in range(2):
            await gate.wait(deadline)
            remaining = deadline - time.monotonic()
            try:
                result = await asyncio.wait_for(client.chat.completions.create(**kwargs),
                    timeout=min(35, remaining) if attempt == 0 else remaining)
                gate.recovered()
                return result
            except Exception as exc:
                status = getattr(exc, 'status_code', None)
                retry = status in {408,429,500,502,503,504} or isinstance(exc, (asyncio.TimeoutError, APIConnectionError))
                delay = .8
                if status == 429:
                    value = getattr(getattr(exc, 'response', None), 'headers', {}).get('Retry-After', '')
                    try:
                        after = float(value)
                    except ValueError:
                        try:
                            after = max(0, parsedate_to_datetime(value).timestamp()-time.time())
                        except (TypeError,ValueError,OverflowError):
                            after = None
                    delay = gate.penalize(after)
                await record('model-retry', 'retrying' if retry and attempt == 0 else 'failed',
                    attempt=attempt+1, http_status=status, delay_ms=round(delay*1000))
                if not retry or attempt or delay+3 >= deadline-time.monotonic():
                    raise
                if status != 429:
                    await asyncio.sleep(delay)
    finally:
        gate.lock.release()
