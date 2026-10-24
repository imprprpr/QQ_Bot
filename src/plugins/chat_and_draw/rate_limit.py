"""Process-wide endpoint admission and 429 recovery, never stores credentials."""
import asyncio
import hashlib
import math
import random
import time
from collections import OrderedDict


class CooldownError(ValueError):
    status = status_code = 429

    def __init__(self, seconds):
        self.retry_after = max(1, math.ceil(seconds))
        super().__init__('MODEL_RATE_COOLDOWN')


class ModelBusyError(ValueError):
    status = status_code = 503


class EndpointGate:
    def __init__(self):
        self.lock = asyncio.Lock()
        self.until = 0.0
        self.strikes = 0

    def penalize(self, retry_after=None):
        self.strikes = min(self.strikes + 1, 8)
        delay = min(120, 8 * 2 ** (self.strikes - 1)) * random.uniform(1, 1.25)
        if retry_after is not None and math.isfinite(retry_after):
            delay = max(delay, min(86400, retry_after))
        self.until = max(self.until, time.monotonic() + delay)
        return self.until - time.monotonic()

    async def wait(self, deadline):
        delay = self.until - time.monotonic()
        if delay > 0:
            if delay + 3 >= deadline - time.monotonic():
                raise CooldownError(delay)
            await asyncio.sleep(delay)

    def recovered(self):
        self.strikes = 0
        self.until = 0


_gates = OrderedDict()


def endpoint_gate(identity):
    key = hashlib.sha256(identity.encode()).digest()
    if key not in _gates:
        # Do not evict active/cooling endpoints, which would bypass recovery.
        for old, gate in list(_gates.items()):
            if len(_gates) < 256:
                break
            if not gate.lock.locked() and gate.until <= time.monotonic():
                _gates.pop(old)
        _gates[key] = EndpointGate()
    _gates.move_to_end(key)
    return _gates[key]
