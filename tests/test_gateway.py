from __future__ import annotations

import copy
import unittest
from datetime import datetime, timedelta, timezone

from agency_os.contracts import finalize_record
from agency_os.gateway import ActionGateway, GatewayDenied, MockPublisher
from agency_os.store import Principal
from agency_os.workflow import run_fictional_article


class AmbiguousPublisher(MockPublisher):
    def publish(self, *, public_fields, idempotency_key):
        self.calls += 1
        raise TimeoutError("synthetic timeout")


class GatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        flow = run_fictional_article()
        self.manifest = flow.records["manifest"]
        self.approval = flow.records["approval"]
        self.principal = Principal(
            "agent_publisher", "publishing-operator", "brand_lantern"
        )
        self.publisher = MockPublisher()
        self.gateway = ActionGateway(
            capability={
                "capability_id": "cap_mock_publish",
                "status": "active",
                "brand_id": "brand_lantern",
                "allowed_role_ids": ["publishing-operator"],
                "destination_ref": "mock_cms:lantern",
                "environment": "sandbox",
                "operation": "publish",
            },
            publisher=self.publisher,
        )
        self.now = datetime.now(timezone.utc)

    def test_duplicate_retry_is_safe_and_rebinding_is_denied(self) -> None:
        first = self.gateway.publish(
            principal=self.principal,
            manifest=self.manifest,
            approval=self.approval,
            idempotency_key="same",
            now=self.now,
        )
        replay = self.gateway.publish(
            principal=self.principal,
            manifest=self.manifest,
            approval=self.approval,
            idempotency_key="same",
            now=self.now,
        )
        self.assertEqual(self.publisher.calls, 1)
        self.assertEqual(first, replay)
        self.assertFalse(first["replayed"])
        self.assertEqual(self.gateway.audit[-1]["outcome"], "ALLOW_IDEMPOTENT_REPLAY")

        other_approval = copy.deepcopy(self.approval)
        other_approval["conditions"] = ["new binding"]
        other_approval = finalize_record(other_approval)
        with self.assertRaises(GatewayDenied):
            self.gateway.publish(
                principal=self.principal,
                manifest=self.manifest,
                approval=other_approval,
                idempotency_key="same",
                now=self.now,
            )
        self.assertEqual(self.publisher.calls, 1)

    def test_changed_expired_or_wrong_destination_is_denied(self) -> None:
        changed = copy.deepcopy(self.manifest)
        changed["public_fields"]["title"] = "Unapproved change"
        changed = finalize_record(changed)
        with self.assertRaises(GatewayDenied):
            self.gateway.publish(
                principal=self.principal,
                manifest=changed,
                approval=self.approval,
                idempotency_key="changed",
                now=self.now,
            )

        expired = copy.deepcopy(self.approval)
        expired["expires_at"] = (self.now - timedelta(seconds=1)).isoformat()
        expired = finalize_record(expired)
        with self.assertRaises(GatewayDenied):
            self.gateway.publish(
                principal=self.principal,
                manifest=self.manifest,
                approval=expired,
                idempotency_key="expired",
                now=self.now,
            )

        wrong_destination = copy.deepcopy(self.manifest)
        wrong_destination["destination_ref"] = "mock_cms:other"
        wrong_destination = finalize_record(wrong_destination)
        with self.assertRaises(GatewayDenied):
            self.gateway.publish(
                principal=self.principal,
                manifest=wrong_destination,
                approval=self.approval,
                idempotency_key="wrong",
                now=self.now,
            )
        self.assertEqual(self.publisher.calls, 0)

    def test_nearest_denied_role_cannot_dispatch(self) -> None:
        producer = Principal("agent_producer", "content-producer", "brand_lantern")
        with self.assertRaises(GatewayDenied):
            self.gateway.publish(
                principal=producer,
                manifest=self.manifest,
                approval=self.approval,
                idempotency_key="wrong-role",
                now=self.now,
            )
        self.assertEqual(self.publisher.calls, 0)

    def test_unknown_result_requires_reconciliation_before_retry(self) -> None:
        publisher = AmbiguousPublisher()
        gateway = ActionGateway(
            capability=self.gateway.capability,
            publisher=publisher,
        )
        for _ in range(2):
            with self.assertRaises(GatewayDenied):
                gateway.publish(
                    principal=self.principal,
                    manifest=self.manifest,
                    approval=self.approval,
                    idempotency_key="ambiguous",
                    now=self.now,
                )
        self.assertEqual(publisher.calls, 1)
        self.assertEqual(gateway.audit[-1]["reason"], "RECONCILIATION_REQUIRED")


if __name__ == "__main__":
    unittest.main()
