from __future__ import annotations

import copy
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from agency_os.contracts import ContractError, finalize_record
from agency_os.gateway import ActionGateway, GatewayDenied, MockPublisher
from agency_os.store import Principal
from agency_os.workflow import run_fictional_article


class AmbiguousPublisher(MockPublisher):
    def publish(self, *, public_fields, idempotency_key):
        self.calls += 1
        raise TimeoutError("synthetic timeout")


class BlockingPublisher(MockPublisher):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def publish(self, *, public_fields, idempotency_key):
        self.entered.set()
        if not self.release.wait(timeout=2):
            raise TimeoutError("test did not release publisher")
        return super().publish(
            public_fields=public_fields, idempotency_key=idempotency_key
        )


class GatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        flow = run_fictional_article()
        self.manifest = flow.records["manifest"]
        self.approval = flow.records["approval"]
        self.store = flow.store
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
            approval_store=self.store,
            approval_authorities={
                "brand_lantern": {"brand_owner": ["human_owner"]}
            },
        )
        self.now = datetime.now(timezone.utc)

    def _persist_approval(self, approval: dict) -> None:
        approver = Principal(
            approval["approver_id"], "human-approver", approval["brand_id"]
        )
        self.store.put(approver, approval)

    def test_duplicate_retry_is_safe_and_rebinding_is_denied(self) -> None:
        first = self.gateway.publish(
            principal=self.principal,
            manifest=self.manifest,
            approval_id=self.approval["approval_id"],
            idempotency_key="same",
            now=self.now,
        )
        replay = self.gateway.publish(
            principal=self.principal,
            manifest=self.manifest,
            approval_id=self.approval["approval_id"],
            idempotency_key="same",
            now=self.now,
        )
        self.assertEqual(self.publisher.calls, 1)
        self.assertEqual(first, replay)
        self.assertFalse(first["replayed"])
        self.assertEqual(self.gateway.audit[-1]["outcome"], "ALLOW_IDEMPOTENT_REPLAY")

        other_approval = copy.deepcopy(self.approval)
        other_approval["approval_id"] = "approval_rebound"
        other_approval["conditions"] = ["new binding"]
        other_approval = finalize_record(other_approval)
        self._persist_approval(other_approval)
        with self.assertRaises(GatewayDenied):
            self.gateway.publish(
                principal=self.principal,
                manifest=self.manifest,
                approval_id=other_approval["approval_id"],
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
                approval_id=self.approval["approval_id"],
                idempotency_key="changed",
                now=self.now,
            )

        expired = copy.deepcopy(self.approval)
        expired["approval_id"] = "approval_expired"
        expired["expires_at"] = (self.now - timedelta(seconds=1)).isoformat()
        expired = finalize_record(expired)
        self._persist_approval(expired)
        with self.assertRaises(GatewayDenied):
            self.gateway.publish(
                principal=self.principal,
                manifest=self.manifest,
                approval_id=expired["approval_id"],
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
                approval_id=self.approval["approval_id"],
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
                approval_id=self.approval["approval_id"],
                idempotency_key="wrong-role",
                now=self.now,
            )
        self.assertEqual(self.publisher.calls, 0)

    def test_unknown_result_requires_reconciliation_before_retry(self) -> None:
        publisher = AmbiguousPublisher()
        gateway = ActionGateway(
            capability=self.gateway.capability,
            publisher=publisher,
            approval_store=self.store,
            approval_authorities=self.gateway.approval_authorities,
        )
        for _ in range(2):
            with self.assertRaises(GatewayDenied):
                gateway.publish(
                    principal=self.principal,
                    manifest=self.manifest,
                    approval_id=self.approval["approval_id"],
                    idempotency_key="ambiguous",
                    now=self.now,
                )
        self.assertEqual(publisher.calls, 1)
        self.assertEqual(gateway.audit[-1]["reason"], "RECONCILIATION_REQUIRED")

    def test_unpersisted_self_issued_and_unknown_authority_are_denied(self) -> None:
        with self.assertRaises(GatewayDenied):
            self.gateway.publish(
                principal=self.principal,
                manifest=self.manifest,
                approval_id="approval_missing",
                idempotency_key="missing",
                now=self.now,
            )

        denied_approvals = (
            ("approval_self", "agent_publisher", "brand_owner"),
            ("approval_unknown_role", "human_owner", "not-authorized"),
            ("approval_unknown_actor", "human_other", "brand_owner"),
        )
        for approval_id, actor_id, authority_role in denied_approvals:
            approval = copy.deepcopy(self.approval)
            approval.update(
                {
                    "approval_id": approval_id,
                    "approver_id": actor_id,
                    "authority_role": authority_role,
                }
            )
            approval = finalize_record(approval)
            self._persist_approval(approval)
            with self.assertRaises(GatewayDenied):
                self.gateway.publish(
                    principal=self.principal,
                    manifest=self.manifest,
                    approval_id=approval_id,
                    idempotency_key=approval_id,
                    now=self.now,
                )
        self.assertEqual(self.publisher.calls, 0)

    def test_cross_brand_approval_reference_is_denied(self) -> None:
        approval = copy.deepcopy(self.approval)
        approval.update(
            {
                "approval_id": "approval_other_brand",
                "brand_id": "brand_other",
                "approver_id": "other_owner",
            }
        )
        approval = finalize_record(approval)
        self._persist_approval(approval)
        with self.assertRaises(GatewayDenied):
            self.gateway.publish(
                principal=self.principal,
                manifest=self.manifest,
                approval_id=approval["approval_id"],
                idempotency_key="cross-brand",
                now=self.now,
            )
        self.assertEqual(self.publisher.calls, 0)

    def test_non_empty_approval_conditions_fail_closed_without_dispatch(self) -> None:
        conditional = copy.deepcopy(self.approval)
        conditional["approval_id"] = "approval_pending_legal"
        conditional["conditions"] = ["DO NOT PUBLISH UNTIL LEGAL SIGN-OFF"]
        conditional = finalize_record(conditional)
        self._persist_approval(conditional)

        with self.assertRaises(GatewayDenied) as denied:
            self.gateway.publish(
                principal=self.principal,
                manifest=self.manifest,
                approval_id=conditional["approval_id"],
                idempotency_key="pending-legal",
                now=self.now,
            )

        self.assertEqual(str(denied.exception), "APPROVAL_CONDITIONS_UNEVALUATED")
        self.assertEqual(self.publisher.calls, 0)
        self.assertEqual(
            self.gateway.audit[-1]["reason"], "APPROVAL_CONDITIONS_UNEVALUATED"
        )

    def test_stored_approval_cannot_be_replaced(self) -> None:
        replacement = copy.deepcopy(self.approval)
        replacement["conditions"] = ["replacement attempt"]
        replacement = finalize_record(replacement)
        with self.assertRaises(ContractError):
            self._persist_approval(replacement)
        stored, provenance = self.store.resolve_approval(
            "brand_lantern", self.approval["approval_id"]
        )
        self.assertEqual(stored, self.approval)
        self.assertEqual(provenance["actor_id"], "human_owner")

    def test_concurrent_callers_dispatch_once(self) -> None:
        publisher = BlockingPublisher()
        gateway = ActionGateway(
            capability=self.gateway.capability,
            publisher=publisher,
            approval_store=self.store,
            approval_authorities=self.gateway.approval_authorities,
        )
        arguments = {
            "principal": self.principal,
            "manifest": self.manifest,
            "approval_id": self.approval["approval_id"],
            "idempotency_key": "concurrent",
            "now": self.now,
        }
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(gateway.publish, **arguments)
            self.assertTrue(publisher.entered.wait(timeout=1))
            second = pool.submit(gateway.publish, **arguments)
            with self.assertRaises(GatewayDenied):
                second.result(timeout=1)
            publisher.release.set()
            receipt = first.result(timeout=1)
        self.assertEqual(receipt["state"], "PUBLISHED")
        self.assertEqual(publisher.calls, 1)


if __name__ == "__main__":
    unittest.main()
