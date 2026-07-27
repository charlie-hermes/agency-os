"""Verified local-runtime identity and fictional credential/egress controls.

The protected host in ``gateway_host`` owns the assertion signer and derives the
caller from Linux peer credentials and ``/proc`` facts. These controls make no
network calls and store no real credential. Production must replace the
fictional process and mock adapter while preserving the fail-closed contracts.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import os
import pwd
import secrets
import socket
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

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

    MAX_ASSERTION_TTL = timedelta(seconds=30)

    def __init__(
        self,
        signing_key: bytes | None = None,
        *,
        max_assertion_ttl: timedelta = MAX_ASSERTION_TTL,
    ) -> None:
        if (
            max_assertion_ttl <= timedelta(0)
            or max_assertion_ttl > self.MAX_ASSERTION_TTL
        ):
            raise ValueError("runtime assertion maximum ttl must be 30 seconds or less")
        self._signing_key = (
            signing_key if signing_key is not None else secrets.token_bytes(32)
        )
        if len(self._signing_key) < 32:
            raise ValueError("runtime signing key must be at least 32 bytes")
        self._authority_instance = secrets.token_hex(16)
        self._max_assertion_ttl = max_assertion_ttl
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
        if ttl > self._max_assertion_ttl:
            raise ValueError("runtime assertion ttl exceeds the configured maximum")
        if now.tzinfo is None:
            raise ValueError("runtime assertion time must be timezone-aware")
        with self._lock:
            enrollment = self._enrollments.get(observation.runtime_id)
        if enrollment is None:
            raise RuntimeIdentityError("RUNTIME_NOT_ENROLLED")
        if enrollment[0] != observation.fingerprint:
            raise RuntimeIdentityError("RUNTIME_IDENTITY_CHANGED")
        body = {
            "assertion_id": assertion_id
            or f"runtime_assertion_{secrets.token_hex(16)}",
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
        if expires_at - issued_at > self._max_assertion_ttl:
            raise RuntimeIdentityError("RUNTIME_ASSERTION_TTL_EXCEEDED")
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
    authorization_guard: Any

    def consume(self, *, destination_ref: str, endpoint: str) -> str:
        return self.broker._consume_lease(
            self, destination_ref=destination_ref, endpoint=endpoint
        )


class FictionalCredentialBroker:
    """Releases a mock credential only inside one exact allowlisted dispatch."""

    MAX_LEASE_TTL = timedelta(seconds=30)

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
        if lease_ttl > self.MAX_LEASE_TTL:
            raise ValueError("credential lease ttl must be 30 seconds or less")
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
        authorization_guard: Any,
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
            authorization_guard=authorization_guard,
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
            authorization_guard.release()
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
        with self._lock:
            active = self._active_leases.pop(lease.lease_id, None)
        if active is None or active[0] != lease:
            raise CredentialBrokerError("CREDENTIAL_LEASE_INVALID_OR_REPLAYED")
        if destination_ref != lease.destination_ref or endpoint != lease.endpoint:
            raise CredentialBrokerError("CREDENTIAL_LEASE_SCOPE_MISMATCH")
        lease.authorization_guard.acquire()
        if lease.expires_at <= self._clock():
            raise CredentialBrokerError("CREDENTIAL_LEASE_EXPIRED")
        return active[1]


def _observe_linux_process(pid: int, uid: int, runtime_id: str) -> RuntimeObservation:
    try:
        executable = Path(f"/proc/{pid}/exe").resolve(strict=True)
        executable_checksum = hashlib.sha256(executable.read_bytes()).hexdigest()
        stat_value = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        stat_fields = stat_value.rsplit(")", 1)[1].split()
        process_start_ticks = stat_fields[19]
        operating_system_user = pwd.getpwuid(uid).pw_name
    except (IndexError, KeyError, OSError, UnicodeDecodeError) as exc:
        raise RuntimeIdentityError("RUNTIME_OS_IDENTITY_UNAVAILABLE") from exc
    return RuntimeObservation(
        runtime_id=runtime_id,
        operating_system_user=operating_system_user,
        executable_checksum=f"sha256:{executable_checksum}",
        instance_nonce=f"linux-pid:{pid}-start:{process_start_ticks}",
    )


def _observe_linux_process_by_pid(
    pid: int, runtime_id: str
) -> RuntimeObservation:
    """Authority-side observation that derives UID from Linux process state."""

    try:
        uid = Path(f"/proc/{pid}").stat().st_uid
    except OSError as exc:
        raise RuntimeIdentityError("RUNTIME_OS_IDENTITY_UNAVAILABLE") from exc
    return _observe_linux_process(pid, uid, runtime_id)


def _read_socket_line(connection: socket.socket, *, maximum: int = 16_384) -> bytes:
    chunks = bytearray()
    while len(chunks) <= maximum:
        chunk = connection.recv(min(4096, maximum + 1 - len(chunks)))
        if not chunk:
            break
        chunks.extend(chunk)
        if b"\n" in chunk:
            break
    if len(chunks) > maximum or b"\n" not in chunks:
        raise RuntimeIdentityError("RUNTIME_MESSAGE_INVALID")
    return bytes(chunks).split(b"\n", 1)[0]


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
