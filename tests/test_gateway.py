from __future__ import annotations

import copy
import inspect
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agency_os.capabilities import CapabilityRegistry, SQLiteCapabilityRegistry
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
from agency_os.runtime_security import (
    CredentialBrokerError,
    FictionalCredentialBroker,
    RuntimeBoundary,
    RuntimeIdentityAuthority,
    RuntimeIdentityError,
    RuntimeObservation,
    VerifiedRuntimeBoundary,
    fictional_credential_broker,
    fictional_credential_grant,
    fictional_runtime,
)
from agency_os.store import Principal
from agency_os.workflow import run_fictional_article


class AmbiguousPublisher(MockPublisher):
    def publish(self, *, public_fields, idempotency_key, credential_lease=None):
        self._authorize_dispatch(credential_lease)
        self.calls += 1
        raise TimeoutError("synthetic timeout")


class PreAuthorizationBlockingPublisher(MockPublisher):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def publish(self, *, public_fields, idempotency_key, credential_lease=None):
        self.entered.set()
        if not self.release.wait(timeout=2):
            raise TimeoutError("test did not release publisher")
        return super().publish(
            public_fields=public_fields,
            idempotency_key=idempotency_key,
            credential_lease=credential_lease,
        )


class BlockingPublisher(MockPublisher):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def publish(self, *, public_fields, idempotency_key, credential_lease=None):
        self._authorize_dispatch(credential_lease)
        self.entered.set()
        if not self.release.wait(timeout=2):
            raise TimeoutError("test did not release publisher")
        self.calls += 1
        external_id = f"mock_{len(self.objects) + 1}"
        result = {
            "external_id": external_id,
            "external_url": f"mock://published/{external_id}",
            "state": "PUBLISHED",
            "rendered_public_fields": copy.deepcopy(dict(public_fields)),
        }
        self.objects[idempotency_key] = result
        return copy.deepcopy(result)


class AdvancingBeforeCredentialPublisher(MockPublisher):
    def __init__(self, clock: "MutableClock", delay: timedelta) -> None:
        super().__init__()
        self.clock = clock
        self.delay = delay

    def publish(self, *, public_fields, idempotency_key, credential_lease=None):
        self.clock.advance(self.delay)
        return super().publish(
            public_fields=public_fields,
            idempotency_key=idempotency_key,
            credential_lease=credential_lease,
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


class MutableClock:
    def __init__(self, current: datetime) -> None:
        self.current = current

    def __call__(self) -> datetime:
        return self.current

    def advance(self, delay: timedelta) -> None:
        self.current += delay


class AdvancingLedger(InMemoryActionLedger):
    def __init__(self, clock: MutableClock, delay: timedelta) -> None:
        super().__init__()
        self.clock = clock
        self.delay = delay

    def reserve(self, brand_id, idempotency_key, request_checksum):
        reservation = super().reserve(brand_id, idempotency_key, request_checksum)
        self.clock.advance(self.delay)
        return reservation


class PausingDispatchRegistry(CapabilityRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.dispatch_ready = threading.Event()
        self.release_dispatch = threading.Event()

    def authorized_dispatch(
        self,
        brand_id,
        capability_id,
        expected_checksum,
        *,
        clock,
        pre_dispatch,
        dispatch,
    ):
        self.dispatch_ready.set()
        if not self.release_dispatch.wait(timeout=2):
            raise TimeoutError("test did not release authorized dispatch")
        return super().authorized_dispatch(
            brand_id,
            capability_id,
            expected_checksum,
            clock=clock,
            pre_dispatch=pre_dispatch,
            dispatch=dispatch,
        )


class TrackingSuspendRegistry(CapabilityRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.suspend_started = threading.Event()

    def suspend(self, issuer, brand_id, capability_id):
        self.suspend_started.set()
        return super().suspend(issuer, brand_id, capability_id)


class ReentrantSuspendingPublisher(MockPublisher):
    def __init__(self, registry, issuer, brand_id, capability_id) -> None:
        super().__init__()
        self.registry = registry
        self.issuer = issuer
        self.brand_id = brand_id
        self.capability_id = capability_id

    def publish(self, *, public_fields, idempotency_key, credential_lease=None):
        self.registry.suspend(
            self.issuer, self.brand_id, self.capability_id
        )
        return super().publish(
            public_fields=public_fields,
            idempotency_key=idempotency_key,
            credential_lease=credential_lease,
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
        clock=None,
        principal=None,
        runtime_boundary: RuntimeBoundary | None = None,
        credential_broker: FictionalCredentialBroker | None = None,
    ) -> ActionGateway:
        runtime_principal = principal or self.principal
        selected_runtime_boundary = runtime_boundary or fictional_runtime(
            runtime_principal
        )
        if runtime_boundary is None:
            self.addCleanup(selected_runtime_boundary.close)
        return ActionGateway(
            capability_id=capability_id,
            capability_registry=capability_registry or self.capability_registry,
            runtime_boundary=selected_runtime_boundary,
            credential_broker=credential_broker
            or fictional_credential_broker(self.capability, clock=clock),
            publisher=publisher,
            approval_store=self.store,
            approval_authorities=self.approval_authorities,
            action_ledger=ledger,
            clock=clock,
        )

    def _persist_approval(self, approval: dict) -> None:
        approver = Principal(
            approval["approver_id"], "human-approver", approval["brand_id"]
        )
        self.store.put(approver, approval)

    def _runtime_observation(self, **changes) -> RuntimeObservation:
        values = {
            "runtime_id": "runtime_agent_publisher",
            "operating_system_user": "local-agent-publisher",
            "executable_checksum": f"sha256:{'b' * 64}",
            "instance_nonce": "instance-publisher-1",
        }
        values.update(changes)
        return RuntimeObservation(**values)

    def test_runtime_assertion_is_required_short_lived_one_use_and_pinned(self) -> None:
        observation = self._runtime_observation()
        authority = RuntimeIdentityAuthority(signing_key=b"r" * 32)
        authority.enroll(observation, self.principal)

        missing_boundary = VerifiedRuntimeBoundary(
            authority,
            observation_source=lambda: observation,
            assertion_source=lambda: None,
        )
        with self.assertRaisesRegex(RuntimeIdentityError, "RUNTIME_ASSERTION_MISSING"):
            missing_boundary.authenticate(now=self.now)

        expired_assertion = authority.issue_assertion(
            observation,
            now=self.now - timedelta(minutes=2),
            ttl=timedelta(seconds=30),
        )
        expired_boundary = VerifiedRuntimeBoundary(
            authority,
            observation_source=lambda: observation,
            assertion_source=lambda: expired_assertion,
        )
        with self.assertRaisesRegex(RuntimeIdentityError, "RUNTIME_ASSERTION_EXPIRED"):
            expired_boundary.authenticate(now=self.now)

        replay_assertion = authority.issue_assertion(observation, now=self.now)
        replay_boundary = VerifiedRuntimeBoundary(
            authority,
            observation_source=lambda: observation,
            assertion_source=lambda: replay_assertion,
        )
        self.assertEqual(replay_boundary.authenticate(now=self.now), self.principal)
        with self.assertRaisesRegex(RuntimeIdentityError, "RUNTIME_ASSERTION_REPLAYED"):
            replay_boundary.authenticate(now=self.now)

        changed_observation = self._runtime_observation(
            instance_nonce="replaced-instance"
        )
        changed_assertion = authority.issue_assertion(observation, now=self.now)
        changed_boundary = VerifiedRuntimeBoundary(
            authority,
            observation_source=lambda: changed_observation,
            assertion_source=lambda: changed_assertion,
        )
        with self.assertRaisesRegex(RuntimeIdentityError, "RUNTIME_IDENTITY_CHANGED"):
            changed_boundary.authenticate(now=self.now)

        pre_restart_assertion = authority.issue_assertion(observation, now=self.now)
        restarted_authority = RuntimeIdentityAuthority(signing_key=b"r" * 32)
        restarted_authority.enroll(observation, self.principal)
        with self.assertRaisesRegex(RuntimeIdentityError, "RUNTIME_ASSERTION_INVALID"):
            restarted_authority.authenticate(
                pre_restart_assertion, observation, now=self.now
            )

        with self.assertRaisesRegex(ValueError, "ttl exceeds"):
            authority.issue_assertion(
                observation, now=self.now, ttl=timedelta(seconds=31)
            )

    def test_structural_runtime_boundary_cannot_supply_gateway_identity(self) -> None:
        class CallerControlledBoundary:
            def authenticate(self):
                return self.principal

        with self.assertRaisesRegex(
            RuntimeIdentityError, "RUNTIME_SUPERVISOR_BOUNDARY_REQUIRED"
        ):
            self._gateway(
                MockPublisher(),
                InMemoryActionLedger(),
                runtime_boundary=CallerControlledBoundary(),
            )

    def test_runtime_enrollment_not_worker_input_drives_identity(self) -> None:
        publish_parameters = inspect.signature(ActionGateway.publish).parameters
        self.assertNotIn("principal", publish_parameters)
        self.assertNotIn("now", publish_parameters)

        wrong_actor = Principal(
            "agent_other_publisher", "publishing-operator", "brand_lantern"
        )
        wrong_actor_gateway = self._gateway(
            MockPublisher(), InMemoryActionLedger(), principal=wrong_actor
        )
        with self.assertRaisesRegex(GatewayDenied, "CAPABILITY_ACTOR_ID_DENIED"):
            wrong_actor_gateway.publish(
                manifest=self.manifest,
                approval_id=self.approval["approval_id"],
                idempotency_key="runtime-wrong-actor",
            )

        wrong_brand = Principal(
            "agent_publisher", "publishing-operator", "brand_other"
        )
        wrong_brand_gateway = self._gateway(
            MockPublisher(), InMemoryActionLedger(), principal=wrong_brand
        )
        with self.assertRaisesRegex(GatewayDenied, "CAPABILITY_NOT_AUTHORITATIVE"):
            wrong_brand_gateway.publish(
                manifest=self.manifest,
                approval_id=self.approval["approval_id"],
                idempotency_key="runtime-wrong-brand",
            )

    def test_credential_scope_allowlist_and_direct_bypass_fail_closed(self) -> None:
        wrong_scope = self._capability(actor_id="agent_other_publisher")
        scope_publisher = MockPublisher()
        scope_gateway = self._gateway(
            scope_publisher,
            InMemoryActionLedger(),
            credential_broker=fictional_credential_broker(wrong_scope),
        )
        with self.assertRaisesRegex(GatewayDenied, "CREDENTIAL_SCOPE_MISMATCH"):
            scope_gateway.publish(
                manifest=self.manifest,
                approval_id=self.approval["approval_id"],
                idempotency_key="credential-scope",
            )

        grant = fictional_credential_grant(self.capability)
        allowlist_publisher = MockPublisher()
        allowlist_gateway = self._gateway(
            allowlist_publisher,
            InMemoryActionLedger(),
            credential_broker=FictionalCredentialBroker(
                [grant],
                egress_allowlist={grant.destination_ref: "mock://cms/other"},
            ),
        )
        with self.assertRaisesRegex(
            GatewayDenied, "EGRESS_DESTINATION_NOT_ALLOWED"
        ):
            allowlist_gateway.publish(
                manifest=self.manifest,
                approval_id=self.approval["approval_id"],
                idempotency_key="egress-not-allowed",
            )

        real_endpoint = "https://cms.example.invalid/publish"
        real_grant = fictional_credential_grant(
            self.capability, endpoint=real_endpoint
        )
        real_publisher = MockPublisher(endpoint=real_endpoint)
        real_gateway = self._gateway(
            real_publisher,
            InMemoryActionLedger(),
            credential_broker=FictionalCredentialBroker(
                [real_grant],
                egress_allowlist={real_grant.destination_ref: real_endpoint},
            ),
        )
        with self.assertRaisesRegex(GatewayDenied, "REAL_NETWORK_EGRESS_DISABLED"):
            real_gateway.publish(
                manifest=self.manifest,
                approval_id=self.approval["approval_id"],
                idempotency_key="real-network-disabled",
            )

        with self.assertRaisesRegex(
            CredentialBrokerError, "DIRECT_ADAPTER_BYPASS_DENIED"
        ):
            MockPublisher().publish(public_fields={}, idempotency_key="bypass")

        self.assertEqual(scope_publisher.calls, 0)
        self.assertEqual(allowlist_publisher.calls, 0)
        self.assertEqual(real_publisher.calls, 0)

    def test_mock_credential_is_short_lived_and_never_returned(self) -> None:
        clock = MutableClock(self.now)
        broker = fictional_credential_broker(self.capability, clock=clock)
        publisher = MockPublisher()
        gateway = self._gateway(
            publisher,
            InMemoryActionLedger(),
            clock=clock,
            credential_broker=broker,
        )
        receipt = gateway.publish(
            manifest=self.manifest,
            approval_id=self.approval["approval_id"],
            idempotency_key="brokered-secret",
        )
        self.assertEqual(publisher.credential_ids, ["credential_mock_lantern"])
        self.assertNotIn("fictional-credential-lantern", repr(receipt))
        self.assertNotIn("fictional-credential-lantern", repr(gateway.audit))
        self.assertNotIn("fictional-credential-lantern", repr(broker.audit))

        grant = fictional_credential_grant(self.capability)
        with self.assertRaisesRegex(ValueError, "30 seconds or less"):
            FictionalCredentialBroker(
                [grant],
                egress_allowlist={grant.destination_ref: grant.endpoint},
                lease_ttl=timedelta(seconds=31),
            )

        expired_grant = replace(
            fictional_credential_grant(self.capability),
            expires_at=self.now - timedelta(seconds=1),
        )
        expired_publisher = MockPublisher()
        expired_gateway = self._gateway(
            expired_publisher,
            InMemoryActionLedger(),
            credential_broker=FictionalCredentialBroker(
                [expired_grant],
                egress_allowlist={
                    expired_grant.destination_ref: expired_grant.endpoint
                },
                clock=clock,
            ),
        )
        with self.assertRaisesRegex(GatewayDenied, "CREDENTIAL_EXPIRED"):
            expired_gateway.publish(
                manifest=self.manifest,
                approval_id=self.approval["approval_id"],
                idempotency_key="expired-credential",
            )
        self.assertEqual(expired_publisher.calls, 0)

    def test_duplicate_retry_is_safe_and_rebinding_is_denied(self) -> None:
        first = self.gateway.publish(
            manifest=self.manifest,
            approval_id=self.approval["approval_id"],
            idempotency_key="same",
        )
        replay = self.gateway.publish(
            manifest=self.manifest,
            approval_id=self.approval["approval_id"],
            idempotency_key="same",
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
                manifest=self.manifest,
                approval_id=other_approval["approval_id"],
                idempotency_key="same",
            )
        self.assertEqual(self.publisher.calls, 1)

    def test_changed_expired_or_wrong_destination_is_denied(self) -> None:
        changed = copy.deepcopy(self.manifest)
        changed["public_fields"]["title"] = "Unapproved change"
        changed = finalize_record(changed)
        with self.assertRaises(GatewayDenied):
            self.gateway.publish(
                manifest=changed,
                approval_id=self.approval["approval_id"],
                idempotency_key="changed",
            )

        expired = copy.deepcopy(self.approval)
        expired["approval_id"] = "approval_expired"
        expired["expires_at"] = (self.now - timedelta(seconds=1)).isoformat()
        expired = finalize_record(expired)
        self._persist_approval(expired)
        with self.assertRaises(GatewayDenied):
            self.gateway.publish(
                manifest=self.manifest,
                approval_id=expired["approval_id"],
                idempotency_key="expired",
            )

        wrong_destination = copy.deepcopy(self.manifest)
        wrong_destination["destination_ref"] = "mock_cms:other"
        wrong_destination = finalize_record(wrong_destination)
        with self.assertRaises(GatewayDenied):
            self.gateway.publish(
                manifest=wrong_destination,
                approval_id=self.approval["approval_id"],
                idempotency_key="wrong",
            )
        self.assertEqual(self.publisher.calls, 0)

    def test_nearest_denied_role_cannot_dispatch(self) -> None:
        producer = Principal("agent_producer", "content-producer", "brand_lantern")
        gateway = self._gateway(
            self.publisher, InMemoryActionLedger(), principal=producer
        )
        with self.assertRaises(GatewayDenied):
            gateway.publish(
                manifest=self.manifest,
                approval_id=self.approval["approval_id"],
                idempotency_key="wrong-role",
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
                manifest=self.manifest,
                approval_id=self.approval["approval_id"],
                idempotency_key="missing-capability",
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
                manifest=self.manifest,
                approval_id=self.approval["approval_id"],
                idempotency_key="wrong-actor-capability",
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
                        manifest=self.manifest,
                        approval_id=self.approval["approval_id"],
                        idempotency_key=capability["capability_id"],
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
                        manifest=self.manifest,
                        approval_id=self.approval["approval_id"],
                        idempotency_key=capability_id,
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
                manifest=self.manifest,
                approval_id=self.approval["approval_id"],
                idempotency_key="suspended-after-reservation",
            )

        self.assertEqual(str(denied.exception), "CAPABILITY_INACTIVE")
        self.assertEqual(publisher.calls, 0)

    def test_capability_expiring_during_reservation_prevents_dispatch(self) -> None:
        clock = MutableClock(self.now)
        capability = self._capability(
            "cap_short_lived",
            expires_at=(self.now + timedelta(milliseconds=20)).isoformat(),
        )
        registry = CapabilityRegistry()
        registry.register(self.director, capability)
        publisher = MockPublisher()
        gateway = self._gateway(
            publisher,
            AdvancingLedger(clock, timedelta(milliseconds=80)),
            capability_id=capability["capability_id"],
            capability_registry=registry,
            clock=clock,
        )

        with self.assertRaises(GatewayDenied) as denied:
            gateway.publish(
                manifest=self.manifest,
                approval_id=self.approval["approval_id"],
                idempotency_key="capability-expired-during-reservation",
            )

        self.assertEqual(str(denied.exception), "CAPABILITY_EXPIRED")
        self.assertEqual(publisher.calls, 0)

    def test_approval_expiring_during_reservation_prevents_dispatch(self) -> None:
        clock = MutableClock(self.now)
        approval = copy.deepcopy(self.approval)
        approval["approval_id"] = "approval_short_lived"
        approval["expires_at"] = (
            self.now + timedelta(milliseconds=20)
        ).isoformat()
        approval = finalize_record(approval)
        self._persist_approval(approval)
        publisher = MockPublisher()
        gateway = self._gateway(
            publisher,
            AdvancingLedger(clock, timedelta(milliseconds=80)),
            clock=clock,
        )

        with self.assertRaises(GatewayDenied) as denied:
            gateway.publish(
                manifest=self.manifest,
                approval_id=approval["approval_id"],
                idempotency_key="approval-expired-during-reservation",
            )

        self.assertEqual(str(denied.exception), "APPROVAL_EXPIRED")
        self.assertEqual(publisher.calls, 0)

    def test_schedule_expiring_during_reservation_prevents_dispatch(self) -> None:
        clock = MutableClock(self.now)
        manifest = copy.deepcopy(self.manifest)
        manifest["schedule_window"] = {
            "starts_at": (self.now - timedelta(minutes=1)).isoformat(),
            "ends_at": (self.now + timedelta(milliseconds=20)).isoformat(),
        }
        manifest = finalize_record(manifest)
        approval = copy.deepcopy(self.approval)
        approval.update(
            {
                "approval_id": "approval_short_schedule",
                "manifest_checksum": manifest["content_checksum"],
                "schedule_window": copy.deepcopy(manifest["schedule_window"]),
                "decided_at": (self.now - timedelta(minutes=1)).isoformat(),
                "expires_at": (self.now + timedelta(minutes=30)).isoformat(),
            }
        )
        approval = finalize_record(approval)
        self._persist_approval(approval)
        publisher = MockPublisher()
        gateway = self._gateway(
            publisher,
            AdvancingLedger(clock, timedelta(milliseconds=80)),
            clock=clock,
        )

        with self.assertRaises(GatewayDenied) as denied:
            gateway.publish(
                manifest=manifest,
                approval_id=approval["approval_id"],
                idempotency_key="schedule-expired-during-reservation",
            )

        self.assertEqual(str(denied.exception), "SCHEDULE_WINDOW_EXPIRED")
        self.assertEqual(publisher.calls, 0)

    def test_final_authorization_rechecks_windows_at_credential_release(self) -> None:
        for window_name in ("approval", "schedule", "capability"):
            with self.subTest(window=window_name):
                clock = MutableClock(self.now)
                manifest = copy.deepcopy(self.manifest)
                approval = copy.deepcopy(self.approval)
                capability = self.capability
                registry = self.capability_registry
                capability_id = capability["capability_id"]
                expected_reason = {
                    "approval": "APPROVAL_EXPIRED",
                    "schedule": "SCHEDULE_WINDOW_EXPIRED",
                    "capability": "CAPABILITY_EXPIRED",
                }[window_name]

                if window_name == "approval":
                    approval["approval_id"] = "approval_final_expiry"
                    approval["expires_at"] = (
                        self.now + timedelta(seconds=1)
                    ).isoformat()
                    approval = finalize_record(approval)
                    self._persist_approval(approval)
                elif window_name == "schedule":
                    manifest["schedule_window"] = {
                        "starts_at": (self.now - timedelta(minutes=1)).isoformat(),
                        "ends_at": (self.now + timedelta(seconds=1)).isoformat(),
                    }
                    manifest = finalize_record(manifest)
                    approval.update(
                        {
                            "approval_id": "approval_final_schedule",
                            "manifest_checksum": manifest["content_checksum"],
                            "schedule_window": copy.deepcopy(
                                manifest["schedule_window"]
                            ),
                            "expires_at": (
                                self.now + timedelta(minutes=30)
                            ).isoformat(),
                        }
                    )
                    approval = finalize_record(approval)
                    self._persist_approval(approval)
                else:
                    capability = self._capability(
                        "cap_final_expiry",
                        expires_at=(self.now + timedelta(seconds=1)).isoformat(),
                    )
                    registry = CapabilityRegistry()
                    registry.register(self.director, capability)
                    capability_id = capability["capability_id"]

                publisher = AdvancingBeforeCredentialPublisher(
                    clock, timedelta(seconds=2)
                )
                broker = fictional_credential_broker(capability, clock=clock)
                gateway = self._gateway(
                    publisher,
                    InMemoryActionLedger(),
                    capability_id=capability_id,
                    capability_registry=registry,
                    clock=clock,
                    credential_broker=broker,
                )
                with self.assertRaises(GatewayDenied) as denied:
                    gateway.publish(
                        manifest=manifest,
                        approval_id=approval["approval_id"],
                        idempotency_key=f"final-{window_name}",
                    )
                self.assertEqual(str(denied.exception), expected_reason)
                self.assertEqual(publisher.calls, 0)
                self.assertEqual(publisher.credential_ids, [])
                self.assertEqual(broker.audit, [])

    def test_suspension_before_credential_release_prevents_adapter_call(self) -> None:
        registry = TrackingSuspendRegistry()
        registry.register(self.director, self.capability)
        publisher = PreAuthorizationBlockingPublisher()
        gateway = self._gateway(
            publisher,
            InMemoryActionLedger(),
            capability_registry=registry,
        )

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                gateway.publish,
                manifest=self.manifest,
                approval_id=self.approval["approval_id"],
                idempotency_key="suspended-before-credential",
            )
            self.assertTrue(publisher.entered.wait(timeout=1))
            registry.suspend(
                self.director,
                "brand_lantern",
                self.capability["capability_id"],
            )
            publisher.release.set()
            with self.assertRaises(GatewayDenied) as denied:
                future.result(timeout=1)

        self.assertEqual(str(denied.exception), "CAPABILITY_INACTIVE")
        self.assertEqual(publisher.calls, 0)
        self.assertEqual(publisher.credential_ids, [])

    def test_suspension_winning_dispatch_race_prevents_adapter_call(self) -> None:
        registry = PausingDispatchRegistry()
        registry.register(self.director, self.capability)
        publisher = MockPublisher()
        gateway = self._gateway(
            publisher,
            InMemoryActionLedger(),
            capability_registry=registry,
        )

        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                gateway.publish,
                manifest=self.manifest,
                approval_id=self.approval["approval_id"],
                idempotency_key="suspension-wins",
            )
            self.assertTrue(registry.dispatch_ready.wait(timeout=1))
            registry.suspend(
                self.director,
                "brand_lantern",
                self.capability["capability_id"],
            )
            registry.release_dispatch.set()
            with self.assertRaises(GatewayDenied) as denied:
                future.result(timeout=1)

        self.assertEqual(str(denied.exception), "CAPABILITY_INACTIVE")
        self.assertEqual(publisher.calls, 0)

    def test_dispatch_winning_race_serializes_suspension_after_adapter(self) -> None:
        registry = TrackingSuspendRegistry()
        registry.register(self.director, self.capability)
        publisher = BlockingPublisher()
        gateway = self._gateway(
            publisher,
            InMemoryActionLedger(),
            capability_registry=registry,
        )

        with ThreadPoolExecutor(max_workers=2) as pool:
            publish_future = pool.submit(
                gateway.publish,
                manifest=self.manifest,
                approval_id=self.approval["approval_id"],
                idempotency_key="dispatch-wins",
            )
            self.assertTrue(publisher.entered.wait(timeout=1))
            suspend_future = pool.submit(
                registry.suspend,
                self.director,
                "brand_lantern",
                self.capability["capability_id"],
            )
            self.assertTrue(registry.suspend_started.wait(timeout=1))
            self.assertFalse(suspend_future.done())
            publisher.release.set()
            receipt = publish_future.result(timeout=1)
            suspend_future.result(timeout=1)

        self.assertEqual(receipt["state"], "PUBLISHED")
        self.assertEqual(publisher.calls, 1)
        _, status = registry.resolve(
            "brand_lantern", self.capability["capability_id"]
        )
        self.assertEqual(status, "suspended")

    def test_adapter_cannot_suspend_and_then_send_reentrantly(self) -> None:
        publisher = ReentrantSuspendingPublisher(
            self.capability_registry,
            self.director,
            "brand_lantern",
            self.capability["capability_id"],
        )
        gateway = self._gateway(publisher, InMemoryActionLedger())

        with self.assertRaises(GatewayDenied) as denied:
            gateway.publish(
                manifest=self.manifest,
                approval_id=self.approval["approval_id"],
                idempotency_key="reentrant-suspension",
            )

        self.assertEqual(str(denied.exception), "CAPABILITY_INACTIVE")
        self.assertEqual(publisher.calls, 0)
        _, status = self.capability_registry.resolve(
            "brand_lantern", self.capability["capability_id"]
        )
        self.assertEqual(status, "suspended")

    def test_unknown_result_requires_reconciliation_before_retry(self) -> None:
        publisher = AmbiguousPublisher()
        gateway = self._gateway(publisher, InMemoryActionLedger())
        for _ in range(2):
            with self.assertRaises(GatewayDenied):
                gateway.publish(
                    manifest=self.manifest,
                    approval_id=self.approval["approval_id"],
                    idempotency_key="ambiguous",
                )
        self.assertEqual(publisher.calls, 1)
        self.assertEqual(gateway.audit[-1]["reason"], "RECONCILIATION_REQUIRED")

    def test_unpersisted_self_issued_and_unknown_authority_are_denied(self) -> None:
        with self.assertRaises(GatewayDenied):
            self.gateway.publish(
                manifest=self.manifest,
                approval_id="approval_missing",
                idempotency_key="missing",
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
                    manifest=self.manifest,
                    approval_id=approval_id,
                    idempotency_key=approval_id,
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
                manifest=self.manifest,
                approval_id=approval["approval_id"],
                idempotency_key="cross-brand",
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
                manifest=self.manifest,
                approval_id=conditional["approval_id"],
                idempotency_key="pending-legal",
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
            "manifest": self.manifest,
            "approval_id": self.approval["approval_id"],
            "idempotency_key": "concurrent",
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
                manifest=self.manifest,
                approval_id=self.approval["approval_id"],
                idempotency_key="invalid-replay",
            )

        self.assertEqual(str(denied.exception), "LEDGER_RECEIPT_INVALID")
        self.assertEqual(publisher.calls, 0)

    def test_unavailable_ledger_fails_closed_before_dispatch(self) -> None:
        publisher = MockPublisher()
        gateway = self._gateway(publisher, UnavailableLedger())

        with self.assertRaises(GatewayDenied) as denied:
            gateway.publish(
                manifest=self.manifest,
                approval_id=self.approval["approval_id"],
                idempotency_key="ledger-unavailable",
            )

        self.assertEqual(str(denied.exception), "LEDGER_UNAVAILABLE")
        self.assertEqual(publisher.calls, 0)

    def test_replaced_durable_authority_in_unsafe_parent_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            parent = Path(temporary_directory)
            authority_path = parent / "capabilities.sqlite3"
            replacement_path = parent / "replacement.sqlite3"
            registry = SQLiteCapabilityRegistry(authority_path)
            registry.register(self.director, self.capability)
            registry.suspend(
                self.director, "brand_lantern", self.capability["capability_id"]
            )
            replacement = SQLiteCapabilityRegistry(replacement_path)
            replacement.register(self.director, self.capability)
            parent.chmod(0o770)
            replacement_path.replace(authority_path)
            publisher = MockPublisher()
            gateway = self._gateway(
                publisher,
                SQLiteActionLedger(self.ledger_path),
                capability_registry=registry,
            )

            try:
                with self.assertRaises(GatewayDenied) as denied:
                    gateway.publish(
                        manifest=self.manifest,
                        approval_id=self.approval["approval_id"],
                        idempotency_key="replaced-authority",
                    )
            finally:
                parent.chmod(0o700)

        self.assertEqual(str(denied.exception), "CAPABILITY_NOT_AUTHORITATIVE")
        self.assertEqual(publisher.calls, 0)

    def test_replaced_durable_ledger_in_unsafe_parent_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            parent = Path(temporary_directory)
            ledger_path = parent / "action-ledger.sqlite3"
            replacement_path = parent / "replacement.sqlite3"
            ledger = SQLiteActionLedger(ledger_path)
            SQLiteActionLedger(replacement_path)
            parent.chmod(0o770)
            replacement_path.replace(ledger_path)
            publisher = MockPublisher()
            gateway = self._gateway(publisher, ledger)

            try:
                with self.assertRaises(GatewayDenied) as denied:
                    gateway.publish(
                        manifest=self.manifest,
                        approval_id=self.approval["approval_id"],
                        idempotency_key="replaced-ledger",
                    )
            finally:
                parent.chmod(0o700)

        self.assertEqual(str(denied.exception), "LEDGER_UNAVAILABLE")
        self.assertEqual(publisher.calls, 0)

    def test_completion_failure_becomes_unknown_and_blocks_retry(self) -> None:
        publisher = MockPublisher()
        gateway = self._gateway(publisher, CompletionFailingLedger())
        arguments = {
            "manifest": self.manifest,
            "approval_id": self.approval["approval_id"],
            "idempotency_key": "completion-failure",
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
            "manifest": self.manifest,
            "approval_id": self.approval["approval_id"],
            "idempotency_key": "shared-concurrent",
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
            "manifest": self.manifest,
            "approval_id": self.approval["approval_id"],
            "idempotency_key": "durable-replay",
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
            "manifest": self.manifest,
            "approval_id": self.approval["approval_id"],
            "idempotency_key": "durable-unknown",
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
            manifest=self.manifest,
            approval_id=self.approval["approval_id"],
            idempotency_key="durable-rebound",
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
                manifest=self.manifest,
                approval_id=replacement["approval_id"],
                idempotency_key="durable-rebound",
            )

        self.assertEqual(str(denied.exception), "IDEMPOTENCY_KEY_REBOUND")
        self.assertEqual(restarted_publisher.calls, 0)


if __name__ == "__main__":
    unittest.main()
