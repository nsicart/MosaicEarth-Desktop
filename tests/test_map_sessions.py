import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient
from homelab import map_sessions as maps


class BrokerTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "private" / "sessions.sqlite3"
        self.now = datetime(2026, 11, 9, 20, tzinfo=timezone.utc).timestamp()
        self.calls = self.auths = 0
        self.fail = self.auth_fail = False
        async def upstream(request):
            await asyncio.sleep(0.001)
            if str(request.url) == maps.TOKEN_URL:
                self.auths += 1
                self.assertEqual(request.method, "POST")
                self.assertIn(b"client_secret=PRIVATE", request.content)
                if self.auth_fail:
                    return httpx.Response(401, json={"error": "PRIVATE"})
                return httpx.Response(200, json={"access_token": "APP-PRIVATE", "expires_in": 3600})
            self.calls += 1
            self.assertEqual(request.headers["authorization"], "Bearer APP-PRIVATE")
            self.assertEqual(request.url.params["durationSeconds"], "43200")
            self.assertNotIn("token", request.url.params)
            if self.fail:
                raise httpx.ReadTimeout("Never expose APP-PRIVATE or URL credentials")
            return httpx.Response(200, json={"sessionToken": "individual-" + str(self.calls), "endTime": (self.now + 43200) * 1000})
        self.transport = httpx.MockTransport(upstream)
        self.broker = self.create()

    def create(self, **kw):
        return maps.SessionBroker(self.path, "ID", "PRIVATE", maximum=kw.pop("maximum", 3),
            billing_day=9, transport=self.transport, clock=lambda: self.now, **kw)

    def tearDown(self):
        self.tmp.cleanup()

    async def test_single_user_coalesces_and_restart_reuses(self):
        values = await asyncio.gather(*(self.broker.session("a" * 64, "peer") for _ in range(12)))
        self.assertEqual(self.calls, 1)
        self.assertTrue(all(x == values[0] for x in values))
        self.auth_fail = True
        self.assertEqual(await self.create().session("a" * 64, "peer"), values[0])
        self.assertNotIn("PRIVATE", str(values))
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        with self.broker.db() as db:
            row = dict(db.execute("SELECT * FROM sessions").fetchone())
        self.assertNotIn("a" * 64, str(row))
        self.assertNotIn("peer", row["peer"])

    async def test_budget_atomic_across_instances_and_cached_at_cap(self):
        one, two = self.create(maximum=1), self.create(maximum=1)
        results = await asyncio.gather(one.session("a" * 64, "peer"), two.session("b" * 64, "peer"), return_exceptions=True)
        self.assertEqual(self.calls, 1)
        self.assertEqual(sum(isinstance(r, maps.SessionUnavailable) and r.code == "budget" for r in results), 1)
        key = "a" if isinstance(results[0], dict) else "b"
        await self.create(maximum=1).session(key * 64, "peer")
        self.assertEqual(self.calls, 1)

    async def test_same_nonce_in_two_processes_never_double_issues(self):
        results = await asyncio.gather(self.broker.session("a" * 64, "peer"), self.create().session("a" * 64, "peer"), return_exceptions=True)
        self.assertEqual(self.calls, 1)
        self.assertTrue(any(isinstance(r, dict) for r in results))
        self.assertTrue(all(isinstance(r, dict) or (isinstance(r, maps.SessionUnavailable) and r.code == "retry") for r in results))

    async def test_uncertain_charge_survives_restart_and_cannot_refund(self):
        self.fail = True
        with self.assertRaises(maps.SessionUnavailable):
            await self.broker.session("a" * 64, "peer")
        self.fail = False
        self.now += 61
        with self.assertRaises(maps.SessionUnavailable) as error:
            await self.create(maximum=1).session("b" * 64, "peer")
        self.assertEqual(error.exception.code, "budget")
        self.assertEqual(self.calls, 1)

    async def test_credentials_outage_does_not_spend(self):
        self.auth_fail = True
        with self.assertRaises(maps.SessionUnavailable):
            await self.broker.session("a" * 64, "peer")
        self.assertEqual(self.calls, 0)
        with self.broker.db() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM usage").fetchone()[0], 0)

    async def test_sessions_are_individual_and_renew_after_expiry(self):
        a = await self.broker.session("a" * 64, "peer")
        b = await self.broker.session("b" * 64, "peer")
        self.assertNotEqual(a["sessionToken"], b["sessionToken"])
        self.now += 43200
        self.assertNotEqual((await self.broker.session("a" * 64, "peer"))["sessionToken"], a["sessionToken"])
        self.assertEqual(self.calls, 3)

    async def test_hour_limit(self):
        broker = self.create(per_hour=1)
        await broker.session("a" * 64, "peer")
        with self.assertRaises(maps.SessionUnavailable) as error:
            await broker.session("b" * 64, "peer")
        self.assertEqual(error.exception.code, "rate")
        self.now += 3601
        await broker.session("b" * 64, "peer")

    def test_billing_boundary(self):
        boundary = datetime(2026, 11, 9, tzinfo=timezone.utc).timestamp()
        self.assertEqual(maps.billing_period(boundary - 1, 9), "2026-10-09")
        self.assertEqual(maps.billing_period(boundary, 9), "2026-11-09")
        self.assertEqual(maps.billing_period(datetime(2027, 1, 1, tzinfo=timezone.utc).timestamp(), 9), "2026-12-09")


class RouterTest(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(maps.router)
        self.client = TestClient(app)
        self.headers = {"Origin": "https://app.mosaicearth.app"}

    def test_bad_origins_and_payloads_never_issue(self):
        with patch.object(maps, "get_broker") as broker:
            for origin in ("", "null", "http://app.mosaicearth.app", "https://app.mosaicearth.app.evil.test", "http://localhost.evil.test", "http://user@localhost:80", "http://localhost:bad"):
                self.assertEqual(self.client.post(maps.SESSION_PATH, headers={"Origin": origin}, json={"client": "a" * 64}).status_code, 403)
            for body in ({}, [], {"client": "a" * 63}, {"client": "z" * 64}):
                self.assertEqual(self.client.post(maps.SESSION_PATH, headers=self.headers, json=body).status_code, 400)
            self.assertEqual(self.client.post(maps.SESSION_PATH, headers=self.headers, json={"extra": "x" * 1024}).status_code, 413)
            self.assertEqual(self.client.post(maps.SESSION_PATH, headers=self.headers, content="{}").status_code, 415)
            broker.assert_not_called()

    def test_desktop_cors_and_no_store(self):
        for origin in ("http://127.0.0.1:18766", "http://localhost:8090", "http://[::1]:8090", self.headers["Origin"]):
            result = self.client.options(maps.SESSION_PATH, headers={"Origin": origin})
            self.assertEqual(result.status_code, 204)
            self.assertEqual(result.headers["access-control-allow-origin"], origin)
            self.assertEqual(result.headers["cache-control"], "no-store")
            self.assertNotIn("access-control-allow-credentials", result.headers)

    def test_no_private_values_in_public_configuration(self):
        with patch.dict(os.environ, {"MOSAICEARTH_ESRI_CLIENT_ID": "SECRET-ID", "MOSAICEARTH_ESRI_CLIENT_SECRET": "SECRET-KEY"}):
            self.assertNotIn("SECRET", maps.public_map_config())


if __name__ == "__main__":
    unittest.main()
