from __future__ import annotations

import json
import subprocess
import unittest
from unittest.mock import patch
from uuid import UUID

from agency_os.contracts import ContractError, finalize_record
from agency_os.fictional_platforms import (
    InMemoryBuzzTransport,
    InMemoryPaperclipBoardTransport,
    InMemoryPaperclipTransport,
)
from agency_os.integrations import (
    BuzzCliTransport,
    IntegrationError,
    PaperclipHTTPTransport,
    PaperclipBoardApprovalAdapter,
    PaperclipBrandBinding,
    PaperclipLifecycleAdapter,
    TypedBuzzAdapter,
)


COMPANY_ID = "00000000-0000-4000-8000-000000000001"


class _Response:
    def __init__(self, value: object) -> None:
        self.raw = json.dumps(value).encode()

    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.raw


class IntegrationTests(unittest.TestCase):
    def test_paperclip_http_transport_is_private_authenticated_and_secret_safe(self) -> None:
        captured = {}

        def opener(request: object, *, timeout: float) -> _Response:
            captured["request"] = request
            captured["timeout"] = timeout
            return _Response({"ok": True})

        transport = PaperclipHTTPTransport(
            base_url="http://172.30.0.1:3100",
            bearer_token="paperclip-secret-canary",
            opener=opener,
        )
        self.assertEqual(transport.request("GET", "/api/health"), {"ok": True})
        request = captured["request"]
        self.assertEqual(request.get_header("Authorization"), "Bearer paperclip-secret-canary")
        self.assertNotIn("paperclip-secret-canary", repr(transport.__dict__.keys()))
        with self.assertRaises(ValueError):
            PaperclipHTTPTransport(base_url="http://example.com", bearer_token="x")
        with self.assertRaises(IntegrationError):
            transport.request("DELETE", "/api/issues/one")

    def test_lifecycle_adapter_uses_exact_routes_and_separate_board_authority(self) -> None:
        transport = InMemoryPaperclipTransport(company_id=COMPANY_ID, brand_id="brand_lantern")
        binding = PaperclipBrandBinding(COMPANY_ID, "brand_lantern")
        adapter = PaperclipLifecycleAdapter(transport, binding)
        board = PaperclipBoardApprovalAdapter(InMemoryPaperclipBoardTransport(transport), binding)
        task = adapter.create_task(
            title="Proof", stage="qa", acceptance_criteria=["pass"],
            idempotency_key="proof-1", artifact_refs=["qa_v1"], status="todo",
        )
        manifest = finalize_record({"brand_id": "brand_lantern", "manifest_id": "m1"})
        approval = adapter.request_approval(issue_ids=[task["id"]], manifest=manifest)
        self.assertEqual(UUID(task["id"]).version, 4)
        create_call = transport.calls[-1]
        self.assertEqual(create_call[:2], ("POST", f"/api/companies/{COMPANY_ID}/approvals"))
        self.assertEqual(create_call[2]["type"], "request_board_approval")
        self.assertNotIn("requestedByAgentId", create_call[2])
        self.assertFalse(hasattr(adapter, "decide_approval"))
        decided = board.decide_approval(
            approval["id"],
            decision="approve",
            decision_note="human approval",
        )
        self.assertEqual(decided["status"], "approved")
        self.assertEqual(UUID(approval["id"]).version, 4)
        with self.assertRaises(IntegrationError):
            board.transport.request("GET", f"/api/approvals/{approval['id']}")
        self.assertEqual(
            [item["id"] for item in adapter.get_approval_issues(approval["id"])],
            [task["id"]],
        )
        event = adapter.record_cost(
            {
                "agentId": "00000000-0000-4000-8000-000000000008",
                "provider": "fictional_fixture",
                "model": "no-model",
                "costCents": 0,
                "occurredAt": "2026-07-28T00:00:00Z",
            }
        )
        self.assertEqual(event["costCents"], 0)
        with self.assertRaises(ContractError):
            adapter.record_cost({"amount": 0, "currency": "USD"})
        transport.issues[task["id"]]["companyId"] = "company_other"
        with self.assertRaises(IntegrationError):
            adapter.get_task(task["id"])
        transport.issues[task["id"]]["companyId"] = COMPANY_ID
        transport.issues[task["id"]]["description"] = transport.issues[task["id"]][
            "description"
        ].replace("brand_lantern", "brand_other")
        with self.assertRaises(IntegrationError):
            adapter.get_task(task["id"])
        with self.assertRaises(ContractError):
            adapter.request_approval(issue_ids=[task["id"]], manifest={"brand_id": "brand_other"})
        with self.assertRaises(ContractError):
            adapter.get_task("pc_100")
        with self.assertRaises(ContractError):
            PaperclipBrandBinding("not-a-uuid", "brand_lantern")

    def test_buzz_adapter_is_private_bounded_and_non_authoritative(self) -> None:
        transport = InMemoryBuzzTransport()
        adapter = TypedBuzzAdapter(transport, "brand_lantern")
        channel = adapter.create_context_channel(campaign_id="camp", purpose="one decision", ttl_seconds=60)
        adapter.post_context(channel["id"], {"brand_id": "brand_lantern", "campaign_id": "camp"})
        message = adapter.post_decision(channel["id"], paperclip_issue_id="pc_1", decision="revise", evidence_refs=["qa_1"])
        self.assertEqual(channel["visibility"], "private")
        self.assertEqual(message["content"]["authority"], "non_authoritative_until_written_to_paperclip")
        self.assertFalse(any("--broadcast" in call for call in transport.calls))
        with self.assertRaises(ContractError):
            adapter.post_context(channel["id"], {"brand_id": "brand_other"})

    def test_buzz_cli_keeps_identity_out_of_arguments_and_errors(self) -> None:
        seen = {}

        def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
            seen["argv"] = argv
            seen["env"] = kwargs["env"]
            return subprocess.CompletedProcess(argv, 0, stdout='{"id":"m1"}', stderr="")

        transport = BuzzCliTransport(relay_url="http://127.0.0.1:9000", private_key_provider=lambda: "buzz-secret-canary")
        with patch("agency_os.integrations.subprocess.run", side_effect=run):
            self.assertEqual(transport.run(["messages", "get", "--channel", "c1"]), {"id": "m1"})
        self.assertNotIn("buzz-secret-canary", " ".join(seen["argv"]))
        self.assertEqual(seen["env"]["BUZZ_PRIVATE_KEY"], "buzz-secret-canary")
        with self.assertRaises(IntegrationError):
            transport.run(["messages", "send", "--broadcast"])

    def test_buzz_denies_unknown_shapes_before_releasing_identity(self) -> None:
        released: list[bool] = []

        def key() -> str:
            released.append(True)
            return "must-not-be-released"

        transport = BuzzCliTransport(relay_url="http://127.0.0.1:9000", private_key_provider=key)
        denied = (
            ["admin", "delete", "--channel", "c1"],
            ["messages", "get", "--channel", "c1", "--channel", "c2"],
            ["messages", "send", "--broadcast"],
        )
        for arguments in denied:
            with self.assertRaises(IntegrationError):
                transport.run(arguments)
        self.assertEqual(released, [])



if __name__ == "__main__":
    unittest.main()
