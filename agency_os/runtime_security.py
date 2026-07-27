"""Verified local-runtime identity and fictional credential/egress controls.

The reference classes model a trusted local supervisor boundary without making
network calls or storing a real credential. A production runtime must replace
the supervisor-issued assertion source and mock adapter while preserving these
fail-closed contracts.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import secrets
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Protocol

from .contracts import ContractError, canonical_bytes, parse_time
from .store import Principal


class RuntimeIdentityError(PermissionError):
    """A trusted runtime assertion could not be authenticated."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CredentialBrokerError(PermissionError):
    """Credential release or egress did not match the exact approved scope."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class RuntimeObservation:
    """Identity facts read by a trusted local supervisor, not by the worker."""

    runtime_id: str
    operating_system_user: str
    executable_checksum: str
    instance_nonce: str

    @property
    def fingerprint(self) -> str:
        body = {
            "runtime_id": self.runtime_id,
            "operating_system_user": self.operating_system_user,
            "executable_checksum": self.executable_checksum,
            "instance_nonce": self.instance_nonce,
        }
        return f"sha256:{hashlib.sha256(canonical_bytes(body)).hexdigest()}"


class RuntimeIdentityAuthority:
    """Pins runtime identity and authenticates signed, one-use assertions."""

    def __init__(self, signing_key: bytes | None = None) -> None:
        self._signing_key = (
            signing_key if signing_key is not None else secrets.token_bytes(32)
        )
        if len(self._signing_key) < 32:
            raise ValueError("runtime signing key must be at least 32 bytes")
        self._authority_instance = secrets.token_hex(16)
        self._enrollments: dict[str, tuple[str, Principal]] = {}
        self._used_assertions: set[str] = set()
        self._lock = threading.RLock()

    def enroll(self, observation: RuntimeObservation, principal: Principal) -> None:
        if observation.runtime_id in self._enrollments:
            raise RuntimeIdentityError("RUNTIME_ENROLLMENT_IMMUTABLE")
        with self._lock:
            if observation.runtime_id in self._enrollments:
                raise RuntimeIdentityError("RUNTIME_ENROLLMENT_IMMUTABLE")
            self._enrollments[observation.runtime_id] = (
                observation.fingerprint,
                principal,
            )

    def issue_assertion(
        self,
        observation: RuntimeObservation,
        *,
        now: datetime,
        ttl: timedelta = timedelta(seconds=30),
        assertion_id: str | None = None,
    ) -> dict[str, str]:
        if ttl <= timedelta(0):
            raise ValueError("runtime assertion ttl must be positive")
        with self._lock:
            enrollment = self._enrollments.get(observation.runtime_id)
        if enrollment is None:
            raise RuntimeIdentityError("RUNTIME_NOT_ENROLLED")
        if enrollment[0] != observation.fingerprint:
            raise RuntimeIdentityError("RUNTIME_IDENTITY_CHANGED")
        body = {
            "assertion_id": assertion_id or f"runtime_assertion_{secrets.token_hex(16)}",
            "runtime_id": observation.runtime_id,
            "runtime_fingerprint": observation.fingerprint,
            "authority_instance": self._authority_instance,
            "issued_at": now.isoformat(),
            "expires_at": (now + ttl).isoformat(),
        }
        body["signature"] = hmac.new(
            self._signing_key, canonical_bytes(body), hashlib.sha256
        ).hexdigest()
        return body

    def authenticate(
        self,
        assertion: Mapping[str, Any] | None,
        observation: RuntimeObservation,
        *,
        now: datetime,
    ) -> Principal:
        if assertion is None:
            raise RuntimeIdentityError("RUNTIME_ASSERTION_MISSING")
        required = {
            "assertion_id",
            "runtime_id",
            "runtime_fingerprint",
            "authority_instance",
            "issued_at",
            "expires_at",
            "signature",
        }
        if set(assertion) != required or any(
            not isinstance(assertion.get(field), str) for field in required
        ):
            raise RuntimeIdentityError("RUNTIME_ASSERTION_INVALID")
        body = copy.deepcopy(dict(assertion))
        signature = body.pop("signature")
        expected_signature = hmac.new(
            self._signing_key, canonical_bytes(body), hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(signature, expected_signature):
            raise RuntimeIdentityError("RUNTIME_ASSERTION_INVALID")
        if body["authority_instance"] != self._authority_instance:
            raise RuntimeIdentityError("RUNTIME_ASSERTION_INVALID")
        try:
            issued_at = parse_time(body["issued_at"])
            expires_at = parse_time(body["expires_at"])
        except ContractError as exc:
            raise RuntimeIdentityError("RUNTIME_ASSERTION_INVALID") from exc
        if issued_at > now:
            raise RuntimeIdentityError("RUNTIME_ASSERTION_NOT_YET_EFFECTIVE")
        if expires_at <= now:
            raise RuntimeIdentityError("RUNTIME_ASSERTION_EXPIRED")
        if body["runtime_id"] != observation.runtime_id:
            raise RuntimeIdentityError("RUNTIME_IDENTITY_CHANGED")
        if body["runtime_fingerprint"] != observation.fingerprint:
            raise RuntimeIdentityError("RUNTIME_IDENTITY_CHANGED")
        with self._lock:
            if body["assertion_id"] in self._used_assertions:
                raise RuntimeIdentityError("RUNTIME_ASSERTION_REPLAYED")
            enrollment = self._enrollments.get(observation.runtime_id)
            if enrollment is None:
                raise RuntimeIdentityError("RUNTIME_NOT_ENROLLED")
            if enrollment[0] != observation.fingerprint:
                raise RuntimeIdentityError("RUNTIME_IDENTITY_CHANGED")
            self._used_assertions.add(body["assertion_id"])
            return enrollment[1]


class RuntimeBoundary(Protocol):
    """Trusted gateway dependency that derives a principal from local runtime."""

    def authenticate(self, *, now: datetime) -> Principal: ...


class VerifiedRuntimeBoundary:
    """Reads both assertion and observation from a trusted supervisor surface."""

    def __init__(
        self,
        authority: RuntimeIdentityAuthority,
        *,
        observation_source: Callable[[], RuntimeObservation],
        assertion_source: Callable[[], Mapping[str, Any] | None],
    ) -> None:
        self._authority = authority
        self._observation_source = observation_source
        self._assertion_source = assertion_source

    def authenticate(self, *, now: datetime) -> Principal:
        return self._authority.authenticate(
            self._assertion_source(), self._observation_source(), now=now
        )


class SupervisorRuntimeBoundary:
    """Local fictional supervisor that renews assertions outside worker input."""

    def __init__(
        self,
        authority: RuntimeIdentityAuthority,
        observation_source: Callable[[], RuntimeObservation],
        *,
        assertion_ttl: timedelta = timedelta(seconds=30),
    ) -> None:
        self._authority = authority
        self._observation_source = observation_source
        self._assertion_ttl = assertion_ttl

    def authenticate(self, *, now: datetime) -> Principal:
        observation = self._observation_source()
        assertion = self._authority.issue_assertion(
            observation, now=now, ttl=self._assertion_ttl
        )
        return self._authority.authenticate(assertion, observation, now=now)


@dataclass(frozen=True)
class FictionalCredentialGrant:
    credential_id: str
    capability_id: str
    capability_checksum: str
    brand_id: str
    actor_id: str
    role_id: str
    destination_ref: str
    environment: str
    operation: str
    endpoint: str
    credential_value: str
    not_before: datetime
    expires_at: datetime


@dataclass(frozen=True)
class _CredentialLease:
    lease_id: str
    credential_id: str
    destination_ref: str
    endpoint: str
    expires_at: datetime
    broker: "FictionalCredentialBroker"

    def consume(self, *, destination_ref: str, endpoint: str) -> str:
        return self.broker._consume_lease(
            self, destination_ref=destination_ref, endpoint=endpoint
        )


class FictionalCredentialBroker:
    """Releases a mock credential only inside one exact allowlisted dispatch."""

    def __init__(
        self,
        grants: list[FictionalCredentialGrant],
        *,
        egress_allowlist: Mapping[str, str],
        clock: Callable[[], datetime] | None = None,
        lease_ttl: timedelta = timedelta(seconds=30),
    ) -> None:
        if lease_ttl <= timedelta(0):
            raise ValueError("credential lease ttl must be positive")
        for grant in grants:
            if grant.not_before.tzinfo is None or grant.expires_at.tzinfo is None:
                raise ValueError("credential grant times must be timezone-aware")
            if grant.not_before >= grant.expires_at:
                raise ValueError("credential grant validity window must be positive")
            required_values = (
                grant.credential_id,
                grant.capability_id,
                grant.capability_checksum,
                grant.brand_id,
                grant.actor_id,
                grant.role_id,
                grant.destination_ref,
                grant.environment,
                grant.operation,
                grant.endpoint,
                grant.credential_value,
            )
            if any(not value for value in required_values):
                raise ValueError("credential grant fields must be non-empty")
        self._grants = {grant.capability_id: grant for grant in grants}
        if len(self._grants) != len(grants):
            raise ValueError("only one credential grant may bind a capability")
        self._egress_allowlist = copy.deepcopy(dict(egress_allowlist))
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._lease_ttl = lease_ttl
        self._active_leases: dict[str, tuple[_CredentialLease, str]] = {}
        self._lock = threading.RLock()
        self.audit: list[dict[str, str]] = []

    def dispatch(
        self,
        *,
        principal: Principal,
        capability: Mapping[str, Any],
        manifest: Mapping[str, Any],
        publisher: Any,
        public_fields: Mapping[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        now = self._clock()
        grant = self._grants.get(str(capability.get("capability_id")))
        if grant is None:
            raise CredentialBrokerError("CREDENTIAL_GRANT_MISSING")
        exact_pairs = (
            (grant.capability_checksum, capability.get("content_checksum")),
            (grant.brand_id, principal.brand_id),
            (grant.actor_id, principal.actor_id),
            (grant.role_id, principal.role_id),
            (grant.destination_ref, manifest.get("destination_ref")),
            (grant.environment, manifest.get("environment")),
            (grant.operation, manifest.get("operation")),
        )
        if any(expected != actual for expected, actual in exact_pairs):
            raise CredentialBrokerError("CREDENTIAL_SCOPE_MISMATCH")
        if grant.not_before > now:
            raise CredentialBrokerError("CREDENTIAL_NOT_YET_EFFECTIVE")
        if grant.expires_at <= now:
            raise CredentialBrokerError("CREDENTIAL_EXPIRED")
        allowed_endpoint = self._egress_allowlist.get(grant.destination_ref)
        if allowed_endpoint != grant.endpoint:
            raise CredentialBrokerError("EGRESS_DESTINATION_NOT_ALLOWED")
        if not grant.endpoint.startswith("mock://"):
            raise CredentialBrokerError("REAL_NETWORK_EGRESS_DISABLED")
        if getattr(publisher, "destination_ref", None) != grant.destination_ref:
            raise CredentialBrokerError("ADAPTER_DESTINATION_MISMATCH")
        if getattr(publisher, "endpoint", None) != grant.endpoint:
            raise CredentialBrokerError("ADAPTER_ENDPOINT_MISMATCH")
        expires_at = min(grant.expires_at, now + self._lease_ttl)
        lease = _CredentialLease(
            lease_id=f"credential_lease_{secrets.token_hex(16)}",
            credential_id=grant.credential_id,
            destination_ref=grant.destination_ref,
            endpoint=grant.endpoint,
            expires_at=expires_at,
            broker=self,
        )
        with self._lock:
            self._active_leases[lease.lease_id] = (lease, grant.credential_value)
        try:
            result = publisher.publish(
                public_fields=public_fields,
                idempotency_key=idempotency_key,
                credential_lease=lease,
            )
        finally:
            with self._lock:
                self._active_leases.pop(lease.lease_id, None)
        self.audit.append(
            {
                "outcome": "ALLOW",
                "credential_id": grant.credential_id,
                "destination_ref": grant.destination_ref,
                "endpoint": grant.endpoint,
            }
        )
        return result

    def _consume_lease(
        self,
        lease: _CredentialLease,
        *,
        destination_ref: str,
        endpoint: str,
    ) -> str:
        now = self._clock()
        with self._lock:
            active = self._active_leases.pop(lease.lease_id, None)
        if active is None or active[0] != lease:
            raise CredentialBrokerError("CREDENTIAL_LEASE_INVALID_OR_REPLAYED")
        if lease.expires_at <= now:
            raise CredentialBrokerError("CREDENTIAL_LEASE_EXPIRED")
        if destination_ref != lease.destination_ref or endpoint != lease.endpoint:
            raise CredentialBrokerError("CREDENTIAL_LEASE_SCOPE_MISMATCH")
        return active[1]


def fictional_runtime(
    principal: Principal,
    *,
    runtime_id: str | None = None,
) -> SupervisorRuntimeBoundary:
    """Construct the local fictional supervisor used by tests and the demo."""

    observation = RuntimeObservation(
        runtime_id=runtime_id or f"runtime_{principal.actor_id}",
        operating_system_user=f"local-{principal.actor_id}",
        executable_checksum=f"sha256:{'a' * 64}",
        instance_nonce=f"instance-{principal.actor_id}",
    )
    authority = RuntimeIdentityAuthority()
    authority.enroll(observation, principal)
    return SupervisorRuntimeBoundary(authority, lambda: observation)


def fictional_credential_broker(
    capability: Mapping[str, Any],
    *,
    clock: Callable[[], datetime] | None = None,
    endpoint: str = "mock://cms/lantern",
    credential_value: str = "fictional-credential-lantern",
) -> FictionalCredentialBroker:
    """Construct a single exact mock grant without a real secret or network."""

    grant = fictional_credential_grant(
        capability,
        endpoint=endpoint,
        credential_value=credential_value,
    )
    return FictionalCredentialBroker(
        [grant],
        egress_allowlist={grant.destination_ref: endpoint},
        clock=clock,
    )


def fictional_credential_grant(
    capability: Mapping[str, Any],
    *,
    endpoint: str = "mock://cms/lantern",
    credential_value: str = "fictional-credential-lantern",
) -> FictionalCredentialGrant:
    """Create an exact mock grant so denial tests can vary one field at a time."""

    return FictionalCredentialGrant(
        credential_id="credential_mock_lantern",
        capability_id=str(capability["capability_id"]),
        capability_checksum=str(capability["content_checksum"]),
        brand_id=str(capability["brand_id"]),
        actor_id=str(capability["actor_id"]),
        role_id=str(capability["role_id"]),
        destination_ref=str(capability["destination_ref"]),
        environment=str(capability["environment"]),
        operation=str(capability["operation"]),
        endpoint=endpoint,
        credential_value=credential_value,
        not_before=parse_time(str(capability["not_before"])),
        expires_at=parse_time(str(capability["expires_at"])),
    )
