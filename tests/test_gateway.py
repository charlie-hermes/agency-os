from __future__ import annotations

import copy
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agency_os.capabilities import CapabilityRegistry
from agency_os.contracts import (
    ContractError,
    finalize_record,
    make_capability_record,
)
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


class SuspendingLedger(InMemoryActionLedger):
    def __init__(self, registry, issuer, brand_id, capability_id):
        super().__init__()
        self.registry = registry
        self.issuer = issuer
        self.brand_id = brand_id
        self.capability_id = capability_id

    def reserve(self, brand_id, idempotency_key, request_checksum):
        reservation = super().reserve(brand_id, idempotency_key, request_checksum)
        self.registry.suspend(
            self.issuer, self.brand_id, self.capability_id
        )
        return reservation


class GatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        flow = run_fictional_article()
        self.manifest = flow.records["manifest"]
        self.approval = flow.records["approval"]
        self.store = flow.store
        self.principal = Principal(
            "agent_publisher", "publishing-operator", "brand_lantern"
        )
        self.director = Principal(
            "agent_director", "agency-director", "brand_lantern"
        )
        self.now = datetime.now(timezone.utc)
        self.capability_registry = CapabilityRegistry()
        self.capability = self._capability()
        self.capability_registry.register(self.director, self.capability)
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

    def _capability(self, capability_id="cap_mock_publish", **changes):
        values = {
            "capability_id": capability_id,
            "brand_id": "brand_lantern",
            "actor_id": "agent_publisher",
            "role_id": "publishing-operator",
            "destination_ref": "mock_cms:lantern",
            "environment": "sandbox",
            "operation": "publish",
            "action_class": "external_write",
            "data_class": "public_content",
            "issued_by": "agent_director",
            "issued_at": (self.now - timedelta(minutes=5)).isoformat(),
            "not_before": (self.now - timedelta(minutes=5)).isoformat(),
            "expires_at": (self.now + timedelta(minutes=30)).isoformat(),
        }
        values.update(changes)
        return make_capability_record(**values)

    def _gateway(
        self,
        publisher,
        ledger,
        capability_id="cap_mock_publish",
        capability_registry=None,
    ) -> ActionGateway:
        return ActionGateway(
            capability_id=capability_id,
            capability_registry=capability_registry or self.capability_registry,
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

    def test_missing_and_wrong_actor_capabilities_are_denied(self) -> None:
        missing_publisher = MockPublisher()
        missing_gateway = self._gateway(
            missing_publisher,
            InMemoryActionLedger(),
            capability_id="cap_missing",
        )
        with self.assertRaises(GatewayDenied) as missing:
            missing_gateway.publish(
                principal=self.principal,
                manifest=self.manifest,
                approval_id=self.approval["approval_id"],
                idempotency_key="missing-capability",
                now=self.now,
            )
        self.assertEqual(str(missing.exception), "CAPABILITY_NOT_AUTHORITATIVE")

        wrong_actor = self._capability(
            "cap_wrong_actor", actor_id="agent_other_publisher"
        )
        self.capability_registry.register(self.director, wrong_actor)
        wrong_actor_publisher = MockPublisher()
        wrong_actor_gateway = self._gateway(
            wrong_actor_publisher,
            InMemoryActionLedger(),
            capability_id=wrong_actor["capability_id"],
        )
        with self.assertRaises(GatewayDenied) as denied:
            wrong_actor_gateway.publish(
                principal=self.principal,
                manifest=self.manifest,
                approval_id=self.approval["approval_id"],
                idempotency_key="wrong-actor-capability",
                now=self.now,
            )
        self.assertEqual(str(denied.exception), "CAPABILITY_ACTOR_ID_DENIED")
        self.assertEqual(missing_publisher.calls, 0)
        self.assertEqual(wrong_actor_publisher.calls, 0)

    def test_suspended_expired_and_future_capabilities_are_denied(self) -> None:
        suspended = self._capability("cap_suspended")
        expired = self._capability(
            "cap_expired",
            issued_at=(self.now - timedelta(minutes=10)).isoformat(),
            not_before=(self.now - timedelta(minutes=10)).isoformat(),
            expires_at=(self.now - timedelta(minutes=1)).isoformat(),
        )
        future = self._capability(
            "cap_future",
            issued_at=(self.now - timedelta(minutes=1)).isoformat(),
            not_before=(self.now + timedelta(minutes=1)).isoformat(),
            expires_at=(self.now + timedelta(minutes=10)).isoformat(),
        )
        for capability in (suspended, expired, future):
            self.capability_registry.register(self.director, capability)
        self.capability_registry.suspend(
            self.director, "brand_lantern", suspended["capability_id"]
        )

        expectations = (
            (suspended, "CAPABILITY_INACTIVE"),
            (expired, "CAPABILITY_EXPIRED"),
            (future, "CAPABILITY_NOT_YET_EFFECTIVE"),
        )
        for capability, reason in expectations:
            with self.subTest(capability=capability["capability_id"]):
                publisher = MockPublisher()
                gateway = self._gateway(
                    publisher,
                    InMemoryActionLedger(),
                    capability_id=capability["capability_id"],
                )
                with self.assertRaises(GatewayDenied) as denied:
                    gateway.publish(
                        principal=self.principal,
                        manifest=self.manifest,
                        approval_id=self.approval["approval_id"],
                        idempotency_key=capability["capability_id"],
                        now=self.now,
                    )
                self.assertEqual(str(denied.exception), reason)
                self.assertEqual(publisher.calls, 0)

    def test_capability_scope_is_exact_for_action_and_data(self) -> None:
        variants = (
            ("destination_ref", "mock_cms:other", "CAPABILITY_DESTINATION_REF_DENIED"),
            ("environment", "production", "CAPABILITY_ENVIRONMENT_DENIED"),
            ("operation", "delete", "CAPABILITY_OPERATION_DENIED"),
            ("action_class", "internal_read", "CAPABILITY_ACTION_CLASS_DENIED"),
            ("data_class", "private_content", "CAPABILITY_DATA_CLASS_DENIED"),
        )
        for field, value, reason in variants:
            with self.subTest(field=field):
                capability_id = f"cap_wrong_{field}"
                capability = self._capability(
                    capability_id, **{field: value}
                )
                self.capability_registry.register(self.director, capability)
                publisher = MockPublisher()
                gateway = self._gateway(
                    publisher,
                    InMemoryActionLedger(),
                    capability_id=capability_id,
                )
                with self.assertRaises(GatewayDenied) as denied:
                    gateway.publish(
                        principal=self.principal,
                        manifest=self.manifest,
                        approval_id=self.approval["approval_id"],
                        idempotency_key=capability_id,
                        now=self.now,
                    )
                self.assertEqual(str(denied.exception), reason)
                self.assertEqual(publisher.calls, 0)

    def test_capability_is_revalidated_after_reservation(self) -> None:
        publisher = MockPublisher()
        ledger = SuspendingLedger(
            self.capability_registry,
            self.director,
            "brand_lantern",
            self.capability["capability_id"],
        )
        gateway = self._gateway(publisher, ledger)

        with self.assertRaises(GatewayDenied) as denied:
            gateway.publish(
                principal=self.principal,
                manifest=self.manifest,
                approval_id=self.approval["approval_id"],
                idempotency_key="suspended-after-reservation",
                now=self.now,
            )

        self.assertEqual(str(denied.exception), "CAPABILITY_INACTIVE")
        self.assertEqual(publisher.calls, 0)

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
