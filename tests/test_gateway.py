from __future__ import annotations

import copy
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agency_os.contracts import ContractError, finalize_record
from agency_os.gateway import ActionGateway, GatewayDenied, MockPublisher
from agency_os.ledger import (
    InMemoryActionLedger,
    LedgerError,
    Reservation,
    SQLiteActionLedger,
)
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


class CompletionFailingLedger(InMemoryActionLedger):
    def complete(self, brand_id, idempotency_key, request_checksum, receipt):
        raise LedgerError("synthetic completion failure")


class UnavailableLedger(InMemoryActionLedger):
    def reserve(self, brand_id, idempotency_key, request_checksum):
        raise LedgerError("synthetic unavailable ledger")


class InvalidReplayLedger(InMemoryActionLedger):
    def reserve(self, brand_id, idempotency_key, request_checksum):
        return Reservation(
            "REPLAY",
            {
                "brand_id": brand_id,
                "idempotency_key": idempotency_key,
                "request_binding_checksum": request_checksum,
                "state": "PUBLISHED",
            },
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
        self.capability = {
            "capability_id": "cap_mock_publish",
            "status": "active",
            "brand_id": "brand_lantern",
            "allowed_role_ids": ["publishing-operator"],
            "destination_ref": "mock_cms:lantern",
            "environment": "sandbox",
            "operation": "publish",
        }
        self.approval_authorities = {
            "brand_lantern": {"brand_owner": ["human_owner"]}
        }
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.ledger_path = Path(temporary_directory.name) / "action-ledger.sqlite3"
        self.publisher = MockPublisher()
        self.gateway = self._gateway(
            self.publisher, InMemoryActionLedger()
        )
        self.now = datetime.now(timezone.utc)

    def _gateway(self, publisher, ledger) -> ActionGateway:
        return ActionGateway(
            capability=self.capability,
            publisher=publisher,
            approval_store=self.store,
            approval_authorities=self.approval_authorities,
            action_ledger=ledger,
        )

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
        gateway = self._gateway(publisher, InMemoryActionLedger())
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
        gateway = self._gateway(publisher, InMemoryActionLedger())
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

    def test_invalid_replay_receipt_fails_closed_before_dispatch(self) -> None:
        publisher = MockPublisher()
        gateway = self._gateway(publisher, InvalidReplayLedger())

        with self.assertRaises(GatewayDenied) as denied:
            gateway.publish(
                principal=self.principal,
                manifest=self.manifest,
                approval_id=self.approval["approval_id"],
                idempotency_key="invalid-replay",
                now=self.now,
            )

        self.assertEqual(str(denied.exception), "LEDGER_RECEIPT_INVALID")
        self.assertEqual(publisher.calls, 0)

    def test_unavailable_ledger_fails_closed_before_dispatch(self) -> None:
        publisher = MockPublisher()
        gateway = self._gateway(publisher, UnavailableLedger())

        with self.assertRaises(GatewayDenied) as denied:
            gateway.publish(
                principal=self.principal,
                manifest=self.manifest,
                approval_id=self.approval["approval_id"],
                idempotency_key="ledger-unavailable",
                now=self.now,
            )

        self.assertEqual(str(denied.exception), "LEDGER_UNAVAILABLE")
        self.assertEqual(publisher.calls, 0)

    def test_completion_failure_becomes_unknown_and_blocks_retry(self) -> None:
        publisher = MockPublisher()
        gateway = self._gateway(publisher, CompletionFailingLedger())
        arguments = {
            "principal": self.principal,
            "manifest": self.manifest,
            "approval_id": self.approval["approval_id"],
            "idempotency_key": "completion-failure",
            "now": self.now,
        }

        with self.assertRaises(GatewayDenied) as first_denial:
            gateway.publish(**arguments)
        with self.assertRaises(GatewayDenied) as retry_denial:
            gateway.publish(**arguments)

        self.assertEqual(str(first_denial.exception), "EXTERNAL_RESULT_UNKNOWN")
        self.assertEqual(str(retry_denial.exception), "RECONCILIATION_REQUIRED")
        self.assertEqual(publisher.calls, 1)

    def test_two_gateway_instances_dispatch_once_with_shared_ledger(self) -> None:
        publisher = BlockingPublisher()
        first_gateway = self._gateway(
            publisher, SQLiteActionLedger(self.ledger_path)
        )
        second_gateway = self._gateway(
            publisher, SQLiteActionLedger(self.ledger_path)
        )
        arguments = {
            "principal": self.principal,
            "manifest": self.manifest,
            "approval_id": self.approval["approval_id"],
            "idempotency_key": "shared-concurrent",
            "now": self.now,
        }

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(first_gateway.publish, **arguments)
            self.assertTrue(publisher.entered.wait(timeout=1))
            second = pool.submit(second_gateway.publish, **arguments)
            with self.assertRaises(GatewayDenied) as denied:
                second.result(timeout=1)
            publisher.release.set()
            receipt = first.result(timeout=1)

        self.assertEqual(str(denied.exception), "RECONCILIATION_REQUIRED")
        self.assertEqual(receipt["state"], "PUBLISHED")
        self.assertEqual(publisher.calls, 1)

    def test_completed_receipt_replays_after_gateway_restart(self) -> None:
        first_publisher = MockPublisher()
        first_gateway = self._gateway(
            first_publisher, SQLiteActionLedger(self.ledger_path)
        )
        arguments = {
            "principal": self.principal,
            "manifest": self.manifest,
            "approval_id": self.approval["approval_id"],
            "idempotency_key": "durable-replay",
            "now": self.now,
        }
        first_receipt = first_gateway.publish(**arguments)

        restarted_publisher = MockPublisher()
        restarted_gateway = self._gateway(
            restarted_publisher, SQLiteActionLedger(self.ledger_path)
        )
        replay = restarted_gateway.publish(**arguments)

        self.assertEqual(first_receipt, replay)
        self.assertEqual(first_publisher.calls, 1)
        self.assertEqual(restarted_publisher.calls, 0)
        self.assertEqual(
            restarted_gateway.audit[-1]["outcome"], "ALLOW_IDEMPOTENT_REPLAY"
        )

    def test_unknown_result_survives_restart_and_blocks_retry(self) -> None:
        ambiguous_publisher = AmbiguousPublisher()
        first_gateway = self._gateway(
            ambiguous_publisher, SQLiteActionLedger(self.ledger_path)
        )
        arguments = {
            "principal": self.principal,
            "manifest": self.manifest,
            "approval_id": self.approval["approval_id"],
            "idempotency_key": "durable-unknown",
            "now": self.now,
        }
        with self.assertRaises(GatewayDenied) as first_denial:
            first_gateway.publish(**arguments)

        restarted_publisher = MockPublisher()
        restarted_gateway = self._gateway(
            restarted_publisher, SQLiteActionLedger(self.ledger_path)
        )
        with self.assertRaises(GatewayDenied) as retry_denial:
            restarted_gateway.publish(**arguments)

        self.assertEqual(str(first_denial.exception), "EXTERNAL_RESULT_UNKNOWN")
        self.assertEqual(str(retry_denial.exception), "RECONCILIATION_REQUIRED")
        self.assertEqual(ambiguous_publisher.calls, 1)
        self.assertEqual(restarted_publisher.calls, 0)

    def test_rebinding_is_denied_after_gateway_restart(self) -> None:
        first_gateway = self._gateway(
            MockPublisher(), SQLiteActionLedger(self.ledger_path)
        )
        first_gateway.publish(
            principal=self.principal,
            manifest=self.manifest,
            approval_id=self.approval["approval_id"],
            idempotency_key="durable-rebound",
            now=self.now,
        )

        replacement = copy.deepcopy(self.approval)
        replacement["approval_id"] = "approval_durable_rebound"
        replacement = finalize_record(replacement)
        self._persist_approval(replacement)
        restarted_publisher = MockPublisher()
        restarted_gateway = self._gateway(
            restarted_publisher, SQLiteActionLedger(self.ledger_path)
        )
        with self.assertRaises(GatewayDenied) as denied:
            restarted_gateway.publish(
                principal=self.principal,
                manifest=self.manifest,
                approval_id=replacement["approval_id"],
                idempotency_key="durable-rebound",
                now=self.now,
            )

        self.assertEqual(str(denied.exception), "IDEMPOTENCY_KEY_REBOUND")
        self.assertEqual(restarted_publisher.calls, 0)


if __name__ == "__main__":
    unittest.main()
