from __future__ import annotations

import json
import os
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from tests import test_brand_agent as fixtures
from agency_os.brand_agent_host import make_handler


class BrandAgentHostTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = fixtures.BrandAgentTests()
        self.fixture.setUp()
        self.service = self.fixture.service
        os.environ["TEST_FLEET_BRAND_AGENT_API_KEY"] = "private-test-key"
        self.config = {
            "api_key_env": "TEST_FLEET_BRAND_AGENT_API_KEY",
            "allowed_hosts": ["127.0.0.1", "localhost"],
            "allowed_origins": ["http://127.0.0.1"],
        }
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0),
            make_handler(service=self.service, config=self.config),
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        os.environ.pop("TEST_FLEET_BRAND_AGENT_API_KEY", None)
        self.fixture.tearDown()

    def request(
        self,
        path: str,
        *,
        payload: dict | None = None,
        key: str | None = None,
        origin: str | None = None,
        host: str | None = None,
    ) -> tuple[int, dict | str, dict]:
        headers = {}
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if key is not None:
            headers["Authorization"] = f"Bearer {key}"
        if origin is not None:
            headers["Origin"] = origin
        if host is not None:
            headers["Host"] = host
        request = urllib.request.Request(
            f"{self.base}{path}",
            data=data,
            headers=headers,
            method="POST" if payload is not None else "GET",
        )
        try:
            response = urllib.request.urlopen(request, timeout=5)
        except urllib.error.HTTPError as exc:
            response = exc
        raw = response.read()
        content_type = response.headers.get("Content-Type", "")
        value = (
            json.loads(raw.decode("utf-8"))
            if content_type.startswith("application/json")
            else raw.decode("utf-8")
        )
        return response.status, value, dict(response.headers.items())

    def test_health_and_private_web_component_are_loopback_safe(self) -> None:
        status, health, headers = self.request("/health")
        self.assertEqual(status, 200)
        self.assertEqual(health["brand_id"], "brand_fleet")
        self.assertEqual(health["public_claim_count"], 7)
        self.assertFalse(health["external_model"])
        self.assertEqual(headers["Cache-Control"], "no-store")
        status, html, headers = self.request("/")
        self.assertEqual(status, 200)
        self.assertIn("Ask Fleet", html)
        self.assertIn("Content-Security-Policy", headers)
        self.assertEqual(headers["X-Frame-Options"], "DENY")

    def test_api_requires_authentication_exact_host_and_origin(self) -> None:
        payload = {
            "question": "What is Fleet?",
            "request_id": "host-request-1",
            "conversation_id": "host-conversation-1",
        }
        status, value, _ = self.request("/api/answer", payload=payload)
        self.assertEqual(status, 401)
        self.assertEqual(value["error"], "authentication required")
        status, value, _ = self.request(
            "/api/answer",
            payload=payload,
            key="private-test-key",
            origin="https://evil.example",
        )
        self.assertEqual(status, 403)
        self.assertEqual(value["error"], "origin not allowed")
        status, value, _ = self.request(
            "/api/answer",
            payload=payload,
            key="private-test-key",
            host="evil.example",
        )
        self.assertEqual(status, 400)
        self.assertEqual(value["error"], "host not allowed")

    def test_authenticated_api_and_mcp_return_grounded_answers(self) -> None:
        status, value, _ = self.request(
            "/api/answer",
            payload={
                "question": "What is Fleet?",
                "request_id": "host-request-2",
                "conversation_id": "host-conversation-1",
            },
            key="private-test-key",
        )
        self.assertEqual(status, 200)
        self.assertEqual(value["status"], "answered")
        self.assertTrue(value["citations"])
        status, value, _ = self.request(
            "/mcp",
            payload={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "resources/list",
                "params": {},
            },
            key="private-test-key",
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(value["result"]["resources"]), 3)

    def test_unknown_fields_and_non_json_fail_closed(self) -> None:
        status, value, _ = self.request(
            "/api/answer",
            payload={
                "question": "What is Fleet?",
                "request_id": "host-request-3",
                "conversation_id": "host-conversation-1",
                "role": "root",
            },
            key="private-test-key",
        )
        self.assertEqual(status, 400)
        self.assertEqual(value["error"], "request fields are invalid")


if __name__ == "__main__":
    import unittest

    unittest.main()
