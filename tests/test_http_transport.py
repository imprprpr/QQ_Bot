"""Phase classification, deadlines and retry rules against loopback HTTP."""
import asyncio
import unittest
from aiohttp import web
from unittest.mock import patch

from test_bot import plugin
from src.plugins.chat_and_draw.http_transport import request_json, HttpFailure


class TransportTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.calls = 0
        self.mode = "ok"
        self.events = []

        async def handler(request):
            await request.json()
            self.calls += 1
            if self.mode == "retry" and self.calls == 1:
                return web.json_response({}, status=503, headers={"Retry-After": "0"})
            if self.mode == "denied":
                return web.json_response({}, status=403)
            if self.mode == "rate":
                return web.json_response({}, status=429, headers={"Retry-After": "120"})
            if self.mode == "slow" and self.calls == 1:
                await asyncio.sleep(.8)
            if self.mode == "slow-retry":
                if self.calls == 1:
                    return web.Response(status=503, headers={"Retry-After": "0"})
                await asyncio.sleep(.35)
            if self.mode == "bad":
                return web.Response(text="not json")
            if self.mode == "large":
                return web.json_response({"content": "x" * 100})
            if self.mode == "auth" and self.calls == 1:
                return web.Response(status=401)
            return web.json_response({"ok": True})

        app = web.Application()
        app.router.add_post("/call", handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.url = f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/call"
        self.forced = []

        async def headers(force):
            self.forced.append(force)
            return {"Authorization": "Bearer NEVER_LOG_THIS"}

        self.headers = headers

    async def asyncTearDown(self):
        await self.runner.cleanup()

    async def observe(self, detail, status):
        self.events.append((detail, status))

    async def call(self, **kwargs):
        return await request_json(self.url, {"secret_prompt": "DO_NOT_LOG"}, self.headers,
                                  timeout=2, observer=self.observe, **kwargs)

    async def test_success_records_safe_phases(self):
        self.assertEqual(await self.call(), {"ok": True})
        detail, status = self.events[-1]
        self.assertEqual(status, "completed")
        self.assertIn("sent_ms", detail)
        self.assertIn("headers_ms", detail)
        self.assertNotIn("NEVER_LOG_THIS", str(self.events))
        self.assertNotIn("DO_NOT_LOG", str(self.events))

    async def test_transient_503_retries_once(self):
        self.mode = "retry"
        self.assertEqual(await self.call(), {"ok": True})
        self.assertEqual(self.calls, 2)
        self.assertEqual([status for _, status in self.events], ["retrying", "completed"])

    async def test_permission_error_does_not_retry(self):
        self.mode = "denied"
        with self.assertRaises(HttpFailure):
            await self.call()
        self.assertEqual(self.calls, 1)

    async def test_retry_after_beyond_budget_does_not_repeat(self):
        self.mode = "rate"
        with self.assertRaises(HttpFailure):
            await self.call()
        self.assertEqual(self.calls, 1)

    async def test_header_wait_timeout_retries_within_overall_budget(self):
        self.mode = "slow"
        self.assertEqual(await self.call(attempt_timeout=.3), {"ok": True})
        self.assertEqual(self.calls, 2)
        self.assertEqual(self.events[0][0]["phase"], "waiting_upstream_or_return_path")

    async def test_invalid_json_does_not_retry(self):
        self.mode = "bad"
        with self.assertRaises(ValueError):
            await self.call()
        self.assertEqual(self.calls, 1)

    async def test_oversized_response_does_not_retry(self):
        self.mode = "large"
        with self.assertRaises(ValueError):
            await self.call(max_bytes=20)
        self.assertEqual(self.calls, 1)

    async def test_401_refreshes_headers_once(self):
        self.mode = "auth"
        self.assertEqual(await self.call(), {"ok": True})
        self.assertEqual(self.forced, [False, True])

    async def test_final_attempt_uses_remaining_total_budget(self):
        self.mode = "slow-retry"
        self.assertEqual(await self.call(attempt_timeout=.1), {"ok": True})
        self.assertEqual(self.calls, 2)

    async def test_single_attempt_still_respects_attempt_budget(self):
        self.mode = "slow"
        with self.assertRaises(asyncio.TimeoutError):
            await self.call(attempt_timeout=.1, max_attempts=1)
        self.assertEqual(self.calls, 1)
