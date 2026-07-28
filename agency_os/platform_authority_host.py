"""Protected local Platform Authority host and worker-only IPC clients.

The independently started host is the only process that receives the
Paperclip SQLite path or approval HMAC key. Worker-facing code receives a
``PlatformAuthorityClient`` containing only a Unix-socket path, an exact bound
principal and an opaque token. It receives no database path, host handle, signer,
verifier or signing key. Evidence and artifact/learning calls use constrained
views of the same client rather than opening SQLite directly.
"""

from __future__ import annotations

import copy
import json
import multiprocessing
import os
import secrets
import socket
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ._approval_authority import (
    _APPROVAL_AUTHORITY_TOKEN,
    _FictionalApprovalAuthority,
    _FictionalRecoveryAuthority,
)
from .contracts import ContractError, canonical_bytes, parse_time
from .platform_adapters import (
    ArtifactStoreError,
    EvidenceStoreError,
    PlatformAdapterError,
    WorkQueueError,
    _AUTHORITY_ADAPTER_TOKEN,
    _DELETION_LEDGER_TOKEN,
    _AuthorityPaperclipAdapter,
    _AuthorityTenantArtifactStore,
    _AuthorityTenantOffboarding,
    _AuthorityTenantRecovery,
    _AuthorityWorkQueue,
    _AuthorityTenantEvidenceStore,
    _SQLiteArtifactDeletionLedger,
)
from .runtime_security import RuntimeIdentityError, _read_socket_line
from .store import AuthorizationError, Principal


class PlatformAuthorityUnavailable(PlatformAdapterError):
    """The protected fictional Platform Authority could not serve a request."""


_MAX_PLATFORM_MESSAGE_BYTES = 4 * 1024 * 1024


class PlatformAuthorityClient:
    """Worker-side Paperclip client with no database path, signer, or key."""

    __slots__ = ("_socket_path", "_principal", "_client_token")

    def __init__(
        self, socket_path: str, principal: Principal, client_token: str
    ) -> None:
        if not socket_path or not client_token:
            raise ValueError("Platform Authority client binding is required")
        self._socket_path = socket_path
        self._principal = principal
        self._client_token = client_token

    def create_task(self, principal: Principal, task: Mapping[str, Any]) -> str:
        return self._request("create_task", principal, task=dict(task))

    def get_task(self, principal: Principal, issue_id: str) -> dict[str, Any]:
        return self._request("get_task", principal, issue_id=issue_id)

    def set_status(
        self,
        principal: Principal,
        issue_id: str,
        expected_checksum: str,
        new_status: str,
    ) -> dict[str, Any]:
        return self._request(
            "set_status",
            principal,
            issue_id=issue_id,
            expected_checksum=expected_checksum,
            new_status=new_status,
        )

    def record_spend(
        self,
        principal: Principal,
        issue_id: str,
        expected_checksum: str,
        amount_minor: int,
    ) -> dict[str, Any]:
        return self._request(
            "record_spend",
            principal,
            issue_id=issue_id,
            expected_checksum=expected_checksum,
            amount_minor=amount_minor,
        )

    def register_approver_policy(
        self, principal: Principal, policy: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._request(
            "register_approver_policy", principal, policy=dict(policy)
        )

    def record_approval(
        self,
        principal: Principal,
        *,
        approval_id: str,
        issue_id: str,
        expected_task_checksum: str,
        policy_id: str,
        policy_revision: int,
        policy_checksum: str,
        decision: str,
        decided_at: str,
        expires_at: str,
    ) -> dict[str, Any]:
        return self._request(
            "record_approval",
            principal,
            approval_id=approval_id,
            issue_id=issue_id,
            expected_task_checksum=expected_task_checksum,
            policy_id=policy_id,
            policy_revision=policy_revision,
            policy_checksum=policy_checksum,
            decision=decision,
            decided_at=decided_at,
            expires_at=expires_at,
        )

    def close_task(
        self,
        principal: Principal,
        issue_id: str,
        expected_checksum: str,
        *,
        evidence_refs: Sequence[str],
        approval_id: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "close_task",
            principal,
            issue_id=issue_id,
            expected_checksum=expected_checksum,
            evidence_refs=list(evidence_refs),
            approval_id=approval_id,
        )

    def record_buzz_context(
        self, principal: Principal, packet: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._request(
            "record_buzz_context", principal, packet=dict(packet)
        )

    def get_buzz_context(
        self, principal: Principal, context_id: str
    ) -> dict[str, Any]:
        return self._request(
            "get_buzz_context", principal, context_id=context_id
        )

    def get_buzz_context_state(
        self, principal: Principal, context_id: str
    ) -> str:
        return self._request(
            "get_buzz_context_state", principal, context_id=context_id
        )

    def archive_buzz_context(
        self, principal: Principal, context_id: str
    ) -> None:
        self._request("archive_buzz_context", principal, context_id=context_id)

    def record_buzz_decision(
        self, principal: Principal, decision: Mapping[str, Any]
    ) -> dict[str, Any]:
        return self._request(
            "record_buzz_decision", principal, decision=dict(decision)
        )

    def get_buzz_decision(
        self, principal: Principal, decision_id: str
    ) -> dict[str, Any]:
        return self._request(
            "get_buzz_decision", principal, decision_id=decision_id
        )

    def audit_events(self, principal: Principal) -> list[dict[str, Any]]:
        return self._request("audit_events", principal)

    def set_audit_retention_policy(
        self,
        principal: Principal,
        *,
        minimum_retention_days: int,
        evidence_ref: str,
    ) -> dict[str, Any]:
        return self._request(
            "set_audit_retention_policy",
            principal,
            minimum_retention_days=minimum_retention_days,
            evidence_ref=evidence_ref,
        )

    def audit_retention_policy(self, principal: Principal) -> dict[str, Any]:
        return self._request("audit_retention_policy", principal)

    def audit_telemetry(self, principal: Principal) -> dict[str, Any]:
        return self._request("audit_telemetry", principal)

    def prepare_audit_expiration(self, principal: Principal) -> dict[str, Any]:
        return self._request("prepare_audit_expiration", principal)

    def expire_audit_events(
        self,
        principal: Principal,
        *,
        manifest: Mapping[str, Any],
        evidence_ref: str,
    ) -> dict[str, Any]:
        return self._request(
            "expire_audit_events",
            principal,
            manifest=dict(manifest),
            evidence_ref=evidence_ref,
        )

    def audit_expiration_receipt(
        self,
        principal: Principal,
        receipt_id: str,
    ) -> dict[str, Any]:
        return self._request(
            "audit_expiration_receipt",
            principal,
            receipt_id=receipt_id,
        )

    def export_tenant_authority(
        self, principal: Principal
    ) -> dict[str, Any]:
        return self._request("export_tenant_authority", principal)

    def restore_tenant_authority(
        self,
        principal: Principal,
        tenant_export: Mapping[str, Any],
    ) -> dict[str, int]:
        return self._request(
            "restore_tenant_authority",
            principal,
            tenant_export=dict(tenant_export),
        )

    def prepare_tenant_offboarding(
        self, principal: Principal
    ) -> dict[str, Any]:
        return self._request("prepare_tenant_offboarding", principal)

    def offboard_tenant(
        self,
        principal: Principal,
        *,
        expected_authority_manifest_checksum: str,
        evidence_ref: str,
    ) -> dict[str, Any]:
        return self._request(
            "offboard_tenant",
            principal,
            expected_authority_manifest_checksum=(
                expected_authority_manifest_checksum
            ),
            evidence_ref=evidence_ref,
        )

    def tenant_offboarding_receipt(
        self,
        principal: Principal,
        receipt_id: str,
    ) -> dict[str, Any]:
        return self._request(
            "tenant_offboarding_receipt",
            principal,
            receipt_id=receipt_id,
        )

    def evidence(self) -> "TenantEvidenceClient":
        return TenantEvidenceClient(self)

    def artifacts(self) -> "TenantArtifactClient":
        return TenantArtifactClient(self)

    def work_queue(self) -> "TenantWorkQueueClient":
        return TenantWorkQueueClient(self)

    def _request(
        self, operation: str, principal: Principal, **arguments: Any
    ) -> Any:
        if principal != self._principal:
            raise AuthorizationError(
                "Platform Authority client principal does not match request"
            )
        request = {
            "operation": operation,
            "client_token": self._client_token,
            "arguments": copy.deepcopy(arguments),
        }
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(3)
                client.connect(self._socket_path)
                client.sendall(canonical_bytes(request) + b"\n")
                response = json.loads(
                    _read_socket_line(
                        client, maximum=_MAX_PLATFORM_MESSAGE_BYTES
                    )
                )
        except (
            OSError,
            ValueError,
            json.JSONDecodeError,
            RuntimeIdentityError,
        ) as exc:
            raise PlatformAuthorityUnavailable(
                "Platform Authority is unavailable"
            ) from exc
        if not isinstance(response, dict):
            raise PlatformAuthorityUnavailable(
                "Platform Authority response is invalid"
            )
        if response.get("outcome") == "ALLOW":
            return copy.deepcopy(response.get("result"))
        _raise_remote_error(response)
        raise AssertionError("unreachable")


class TenantEvidenceClient:
    """Worker-side evidence view backed by the protected authority host."""

    __slots__ = ("_authority",)

    def __init__(self, authority: PlatformAuthorityClient) -> None:
        self._authority = authority

    def put(self, principal: Principal, evidence: Mapping[str, Any]) -> str:
        return self._authority._request(
            "put_evidence", principal, evidence=dict(evidence)
        )

    def get(self, principal: Principal, evidence_id: str) -> dict[str, Any]:
        return self._authority._request(
            "get_evidence", principal, evidence_id=evidence_id
        )

    def list_for_issue(
        self, principal: Principal, paperclip_issue_id: str
    ) -> list[dict[str, Any]]:
        return self._authority._request(
            "list_evidence_for_issue",
            principal,
            paperclip_issue_id=paperclip_issue_id,
        )


class TenantArtifactClient:
    """Worker-side artifact and learning view backed by the authority host."""

    __slots__ = ("_authority",)

    def __init__(self, authority: PlatformAuthorityClient) -> None:
        self._authority = authority

    def put(self, principal: Principal, artifact: Mapping[str, Any]) -> str:
        return self._authority._request(
            "put_artifact", principal, artifact=dict(artifact)
        )

    def get(self, principal: Principal, record_id: str) -> dict[str, Any]:
        return self._authority._request(
            "get_artifact", principal, record_id=record_id
        )

    def active_learning(self, principal: Principal) -> list[dict[str, Any]]:
        return self._authority._request("active_learning", principal)

    def export_tenant(self, principal: Principal) -> dict[str, Any]:
        return self._authority._request("export_artifacts", principal)

    def restore_tenant(
        self, principal: Principal, tenant_export: Mapping[str, Any]
    ) -> int:
        return self._authority._request(
            "restore_artifacts",
            principal,
            tenant_export=dict(tenant_export),
        )

    def delete_tenant(
        self, principal: Principal, expected_export_checksum: str
    ) -> dict[str, Any]:
        return self._authority._request(
            "delete_artifacts",
            principal,
            expected_export_checksum=expected_export_checksum,
        )

    def deletion_receipt(
        self, principal: Principal, receipt_id: str
    ) -> dict[str, Any]:
        return self._authority._request(
            "artifact_deletion_receipt",
            principal,
            receipt_id=receipt_id,
        )


class TenantWorkQueueClient:
    """Worker-side durable queue view backed by the protected authority host."""

    __slots__ = ("_authority",)

    def __init__(self, authority: PlatformAuthorityClient) -> None:
        self._authority = authority

    def enqueue(self, principal: Principal, work_item: Mapping[str, Any]) -> str:
        return self._authority._request(
            "enqueue_work", principal, work_item=dict(work_item)
        )

    def lease_next(
        self, principal: Principal, lease_seconds: int
    ) -> dict[str, Any] | None:
        return self._authority._request(
            "lease_work", principal, lease_seconds=lease_seconds
        )

    def heartbeat(
        self,
        principal: Principal,
        work_item_id: str,
        lease_token: str,
        lease_seconds: int,
    ) -> dict[str, Any]:
        return self._authority._request(
            "heartbeat_work",
            principal,
            work_item_id=work_item_id,
            lease_token=lease_token,
            lease_seconds=lease_seconds,
        )

    def complete(
        self, principal: Principal, work_item_id: str, lease_token: str
    ) -> dict[str, Any]:
        return self._authority._request(
            "complete_work",
            principal,
            work_item_id=work_item_id,
            lease_token=lease_token,
        )

    def fail(
        self,
        principal: Principal,
        work_item_id: str,
        lease_token: str,
        *,
        error_class: str,
        retryable: bool,
        external_result: str,
    ) -> dict[str, Any]:
        return self._authority._request(
            "fail_work",
            principal,
            work_item_id=work_item_id,
            lease_token=lease_token,
            error_class=error_class,
            retryable=retryable,
            external_result=external_result,
        )

    def reconcile(
        self,
        principal: Principal,
        work_item_id: str,
        *,
        outcome: str,
        evidence_ref: str,
        disposition: str,
    ) -> dict[str, Any]:
        return self._authority._request(
            "reconcile_work",
            principal,
            work_item_id=work_item_id,
            outcome=outcome,
            evidence_ref=evidence_ref,
            disposition=disposition,
        )

    def record_dead_letter_disposition(
        self,
        principal: Principal,
        work_item_id: str,
        *,
        evidence_ref: str,
        disposition: str,
    ) -> dict[str, Any]:
        return self._authority._request(
            "disposition_dead_letter",
            principal,
            work_item_id=work_item_id,
            evidence_ref=evidence_ref,
            disposition=disposition,
        )

    def get(self, principal: Principal, work_item_id: str) -> dict[str, Any]:
        return self._authority._request(
            "get_work", principal, work_item_id=work_item_id
        )

    def dead_letters(self, principal: Principal) -> list[dict[str, Any]]:
        return self._authority._request("list_dead_letters", principal)

    def cancel_tenant(
        self,
        principal: Principal,
        *,
        evidence_ref: str,
    ) -> dict[str, Any]:
        return self._authority._request(
            "cancel_tenant_work",
            principal,
            evidence_ref=evidence_ref,
        )

    def cancellation_receipt(
        self,
        principal: Principal,
        receipt_id: str,
    ) -> dict[str, Any]:
        return self._authority._request(
            "queue_cancellation_receipt",
            principal,
            receipt_id=receipt_id,
        )


def _raise_remote_error(response: Mapping[str, Any]) -> None:
    message = str(response.get("message", "Platform Authority denied request"))
    error_type = response.get("error_type")
    if error_type == "AuthorizationError":
        raise AuthorizationError(message)
    if error_type == "ContractError":
        raise ContractError(message)
    if error_type == "EvidenceStoreError":
        raise EvidenceStoreError(message)
    if error_type == "ArtifactStoreError":
        raise ArtifactStoreError(message)
    if error_type == "WorkQueueError":
        raise WorkQueueError(message)
    if error_type == "KeyError":
        subject = response.get("subject")
        raise KeyError(subject if isinstance(subject, str) else message)
    if error_type == "PlatformAdapterError":
        raise PlatformAdapterError(message)
    raise PlatformAuthorityUnavailable(message)


class _AuthorityClock:
    def __init__(self, initial: datetime | None) -> None:
        self._override = initial

    def __call__(self) -> datetime:
        if self._override is None:
            return datetime.now(timezone.utc)
        return self._override

    def set(self, value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("authority time must be timezone-aware")
        self._override = value


_PLATFORM_HOST_TOKEN = object()


class _PlatformAuthorityHost:
    """Authority-only lifecycle handle; never passed to a worker."""

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        *,
        deletion_ledger_path: str | os.PathLike[str],
        initialize_deletion_ledger: bool,
        authority_id: str,
        approval_signing_key: bytes,
        timeout_seconds: float,
        initial_time: datetime | None,
        principals: Sequence[Principal],
        _construction_token: object,
    ) -> None:
        if _construction_token is not _PLATFORM_HOST_TOKEN:
            raise PlatformAuthorityUnavailable(
                "Platform Authority host construction denied"
            )
        self._client_tokens: dict[Principal, str] = {}
        client_principals: dict[str, Principal] = {}
        for principal in principals:
            if not isinstance(principal, Principal) or not all(
                (principal.actor_id, principal.role_id, principal.brand_id)
            ):
                raise ValueError(
                    "Platform Authority principal fields must be non-empty"
                )
            if principal in self._client_tokens:
                raise ValueError("Platform Authority principals must be unique")
            token = secrets.token_hex(32)
            while token in client_principals:
                token = secrets.token_hex(32)
            self._client_tokens[principal] = token
            client_principals[token] = principal
        if not client_principals:
            raise ValueError("Platform Authority requires a provisioned principal")
        self._temporary_directory = tempfile.TemporaryDirectory(
            prefix="agency-os-platform-authority-"
        )
        self.socket_path = str(
            Path(self._temporary_directory.name) / "paperclip.sock"
        )
        context = multiprocessing.get_context("fork")
        self._control_parent, control_child = context.Pipe(duplex=True)
        self._process = context.Process(
            target=_run_platform_authority_host,
            args=(
                self.socket_path,
                str(database_path),
                str(deletion_ledger_path),
                initialize_deletion_ledger,
                authority_id,
                bytes(approval_signing_key),
                timeout_seconds,
                initial_time,
                client_principals,
                control_child,
            ),
            daemon=True,
        )
        self._closed = False
        self._process.start()
        control_child.close()
        if not self._control_parent.poll(3):
            self.close()
            raise PlatformAuthorityUnavailable(
                "Platform Authority host did not start"
            )
        outcome = self._control_parent.recv()
        if outcome != "READY":
            self.close()
            raise PlatformAuthorityUnavailable(str(outcome))

    def client(self, principal: Principal) -> PlatformAuthorityClient:
        if self._closed or not self._process.is_alive():
            raise PlatformAuthorityUnavailable(
                "Platform Authority is unavailable"
            )
        token = self._client_tokens.get(principal)
        if token is None:
            raise AuthorizationError(
                "Platform Authority principal is not provisioned"
            )
        return PlatformAuthorityClient(self.socket_path, principal, token)

    def set_time(self, value: datetime) -> None:
        if self._closed or not self._process.is_alive():
            raise PlatformAuthorityUnavailable(
                "Platform Authority is unavailable"
            )
        if value.tzinfo is None:
            raise ValueError("authority time must be timezone-aware")
        self._control_parent.send(
            {"operation": "set_time", "value": value.isoformat()}
        )
        if not self._control_parent.poll(3):
            raise PlatformAuthorityUnavailable(
                "Platform Authority is unavailable"
            )
        if self._control_parent.recv() != "TIME_SET":
            raise PlatformAuthorityUnavailable(
                "Platform Authority rejected its control request"
            )

    def close(self) -> None:
        if self._closed:
            return
        try:
            if self._process.is_alive():
                self._control_parent.send({"operation": "shutdown"})
                if self._control_parent.poll(2):
                    self._control_parent.recv()
        except (BrokenPipeError, EOFError, OSError):
            pass
        self._process.join(timeout=2)
        if self._process.is_alive():
            self._process.terminate()
            self._process.join(timeout=2)
        self._control_parent.close()
        self._temporary_directory.cleanup()
        self._client_tokens.clear()
        self._closed = True

    def __enter__(self) -> "_PlatformAuthorityHost":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _provision_platform_authority_host(
    database_path: str | os.PathLike[str],
    *,
    deletion_ledger_path: str | os.PathLike[str],
    initialize_deletion_ledger: bool = False,
    authority_id: str,
    approval_signing_key: bytes,
    timeout_seconds: float = 5.0,
    initial_time: datetime | None = None,
    principals: Sequence[Principal],
) -> _PlatformAuthorityHost:
    """Authority bootstrap only: start the protected Paperclip host."""

    return _PlatformAuthorityHost(
        database_path,
        deletion_ledger_path=deletion_ledger_path,
        initialize_deletion_ledger=initialize_deletion_ledger,
        authority_id=authority_id,
        approval_signing_key=approval_signing_key,
        timeout_seconds=timeout_seconds,
        initial_time=initial_time,
        principals=principals,
        _construction_token=_PLATFORM_HOST_TOKEN,
    )


def _run_platform_authority_host(
    socket_path: str,
    database_path: str,
    deletion_ledger_path: str,
    initialize_deletion_ledger: bool,
    authority_id: str,
    approval_signing_key: bytes,
    timeout_seconds: float,
    initial_time: datetime | None,
    client_principals: Mapping[str, Principal],
    control: Any,
) -> None:
    authority_clock = _AuthorityClock(initial_time)
    try:
        signer = _FictionalApprovalAuthority(
            authority_id=authority_id,
            signing_key=approval_signing_key,
            _construction_token=_APPROVAL_AUTHORITY_TOKEN,
        )
        recovery_authority = _FictionalRecoveryAuthority(
            authority_id=authority_id,
            signing_key=approval_signing_key,
            _construction_token=_APPROVAL_AUTHORITY_TOKEN,
        )
        full_recovery_authority = _FictionalRecoveryAuthority(
            authority_id=authority_id,
            signing_key=approval_signing_key,
            scope="authority",
            _construction_token=_APPROVAL_AUTHORITY_TOKEN,
        )
        deletion_ledger = _SQLiteArtifactDeletionLedger(
            deletion_ledger_path,
            authority_id=authority_id,
            timeout_seconds=timeout_seconds,
            allow_create=initialize_deletion_ledger,
            _construction_token=_DELETION_LEDGER_TOKEN,
        )
        paperclip = _AuthorityPaperclipAdapter(
            database_path,
            timeout_seconds=timeout_seconds,
            clock=authority_clock,
            approval_authority=signer,
            _construction_token=_AUTHORITY_ADAPTER_TOKEN,
        )
        evidence = _AuthorityTenantEvidenceStore(
            database_path,
            timeout_seconds=timeout_seconds,
            _construction_token=_AUTHORITY_ADAPTER_TOKEN,
        )
        artifacts = _AuthorityTenantArtifactStore(
            database_path,
            timeout_seconds=timeout_seconds,
            clock=authority_clock,
            recovery_authority=recovery_authority,
            deletion_ledger=deletion_ledger,
            _construction_token=_AUTHORITY_ADAPTER_TOKEN,
        )
        work_queue = _AuthorityWorkQueue(
            database_path,
            timeout_seconds=timeout_seconds,
            clock=authority_clock,
            _construction_token=_AUTHORITY_ADAPTER_TOKEN,
        )
        tenant_offboarding = _AuthorityTenantOffboarding(
            database_path,
            timeout_seconds=timeout_seconds,
            clock=authority_clock,
            artifacts=artifacts,
            deletion_ledger=deletion_ledger,
            _construction_token=_AUTHORITY_ADAPTER_TOKEN,
        )
        tenant_recovery = _AuthorityTenantRecovery(
            database_path,
            timeout_seconds=timeout_seconds,
            clock=authority_clock,
            recovery_authority=full_recovery_authority,
            artifacts=artifacts,
            _construction_token=_AUTHORITY_ADAPTER_TOKEN,
        )
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(socket_path)
            os.chmod(socket_path, 0o600)
            server.listen(8)
            server.settimeout(0.1)
            control.send("READY")
            while True:
                if control.poll():
                    command = control.recv()
                    if command == {"operation": "shutdown"}:
                        control.send("CLOSED")
                        return
                    if (
                        isinstance(command, dict)
                        and command.get("operation") == "set_time"
                        and isinstance(command.get("value"), str)
                    ):
                        authority_clock.set(parse_time(command["value"]))
                        control.send("TIME_SET")
                    else:
                        control.send("AUTHORITY_CONTROL_INVALID")
                try:
                    connection, _ = server.accept()
                except TimeoutError:
                    continue
                with connection:
                    response = _handle_platform_request(
                        connection,
                        paperclip,
                        evidence,
                        artifacts,
                        work_queue,
                        tenant_recovery,
                        tenant_offboarding,
                        client_principals,
                    )
                    connection.sendall(canonical_bytes(response) + b"\n")
    except Exception:
        try:
            control.send("PLATFORM_AUTHORITY_UNAVAILABLE")
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        control.close()


_PAPERCLIP_OPERATIONS = frozenset(
    {
        "create_task",
        "get_task",
        "set_status",
        "record_spend",
        "register_approver_policy",
        "record_approval",
        "close_task",
        "record_buzz_context",
        "get_buzz_context",
        "get_buzz_context_state",
        "archive_buzz_context",
        "record_buzz_decision",
        "get_buzz_decision",
        "audit_events",
        "set_audit_retention_policy",
        "audit_retention_policy",
        "audit_telemetry",
        "prepare_audit_expiration",
        "expire_audit_events",
        "audit_expiration_receipt",
    }
)


def _handle_platform_request(
    connection: socket.socket,
    paperclip: _AuthorityPaperclipAdapter,
    evidence: _AuthorityTenantEvidenceStore,
    artifacts: _AuthorityTenantArtifactStore,
    work_queue: _AuthorityWorkQueue,
    tenant_recovery: _AuthorityTenantRecovery,
    tenant_offboarding: _AuthorityTenantOffboarding,
    client_principals: Mapping[str, Principal],
) -> dict[str, Any]:
    try:
        request = json.loads(
            _read_socket_line(
                connection, maximum=_MAX_PLATFORM_MESSAGE_BYTES
            )
        )
        if not isinstance(request, dict) or set(request) != {
            "operation",
            "client_token",
            "arguments",
        }:
            raise ContractError("Platform Authority request is invalid")
        operation = request["operation"]
        client_token = request["client_token"]
        arguments = request["arguments"]
        if (
            not isinstance(operation, str)
            or not isinstance(client_token, str)
            or not isinstance(arguments, dict)
        ):
            raise ContractError("Platform Authority request is invalid")
        principal = client_principals.get(client_token)
        if principal is None:
            raise AuthorizationError(
                "Platform Authority client binding is not provisioned"
            )
        post_offboarding_operations = {
            "offboard_tenant",
            "tenant_offboarding_receipt",
            "artifact_deletion_receipt",
            "queue_cancellation_receipt",
            "audit_expiration_receipt",
        }
        if (
            operation not in post_offboarding_operations
            and tenant_offboarding.is_offboarded(principal.brand_id)
        ):
            raise AuthorizationError(
                "tenant Platform Authority access is closed after offboarding"
            )
        if operation in _PAPERCLIP_OPERATIONS:
            result = getattr(paperclip, operation)(principal, **arguments)
        elif operation == "put_evidence":
            result = evidence.put(principal, **arguments)
        elif operation == "get_evidence":
            result = evidence.get(principal, **arguments)
        elif operation == "list_evidence_for_issue":
            result = evidence.list_for_issue(principal, **arguments)
        elif operation == "put_artifact":
            result = artifacts.put(principal, **arguments)
        elif operation == "get_artifact":
            result = artifacts.get(principal, **arguments)
        elif operation == "active_learning":
            result = artifacts.active_learning(principal, **arguments)
        elif operation == "export_artifacts":
            result = artifacts.export_tenant(principal, **arguments)
        elif operation == "restore_artifacts":
            result = artifacts.restore_tenant(principal, **arguments)
        elif operation == "delete_artifacts":
            result = artifacts.delete_tenant(principal, **arguments)
        elif operation == "artifact_deletion_receipt":
            result = artifacts.deletion_receipt(principal, **arguments)
        elif operation == "enqueue_work":
            result = work_queue.enqueue(principal, **arguments)
        elif operation == "lease_work":
            result = work_queue.lease_next(principal, **arguments)
        elif operation == "heartbeat_work":
            result = work_queue.heartbeat(principal, **arguments)
        elif operation == "complete_work":
            result = work_queue.complete(principal, **arguments)
        elif operation == "fail_work":
            result = work_queue.fail(principal, **arguments)
        elif operation == "reconcile_work":
            result = work_queue.reconcile(principal, **arguments)
        elif operation == "disposition_dead_letter":
            result = work_queue.record_dead_letter_disposition(
                principal, **arguments
            )
        elif operation == "get_work":
            result = work_queue.get(principal, **arguments)
        elif operation == "list_dead_letters":
            result = work_queue.dead_letters(principal, **arguments)
        elif operation == "cancel_tenant_work":
            result = work_queue.cancel_tenant(principal, **arguments)
        elif operation == "queue_cancellation_receipt":
            result = work_queue.cancellation_receipt(principal, **arguments)
        elif operation == "export_tenant_authority":
            result = tenant_recovery.export_tenant(principal, **arguments)
        elif operation == "restore_tenant_authority":
            result = tenant_recovery.restore_tenant(principal, **arguments)
        elif operation == "prepare_tenant_offboarding":
            result = tenant_offboarding.prepare(principal, **arguments)
        elif operation == "offboard_tenant":
            result = tenant_offboarding.offboard(principal, **arguments)
        elif operation == "tenant_offboarding_receipt":
            result = tenant_offboarding.receipt(principal, **arguments)
        else:
            raise ContractError("Platform Authority operation is not allowed")
        return {"outcome": "ALLOW", "result": result}
    except KeyError as exc:
        subject = exc.args[0] if exc.args and isinstance(exc.args[0], str) else ""
        return {
            "outcome": "DENY",
            "error_type": "KeyError",
            "message": "Platform Authority record was not found",
            "subject": subject,
        }
    except AuthorizationError as exc:
        return {
            "outcome": "DENY",
            "error_type": "AuthorizationError",
            "message": str(exc),
        }
    except ContractError as exc:
        return {
            "outcome": "DENY",
            "error_type": "ContractError",
            "message": str(exc),
        }
    except EvidenceStoreError as exc:
        return {
            "outcome": "DENY",
            "error_type": "EvidenceStoreError",
            "message": str(exc),
        }
    except ArtifactStoreError as exc:
        return {
            "outcome": "DENY",
            "error_type": "ArtifactStoreError",
            "message": str(exc),
        }
    except WorkQueueError as exc:
        return {
            "outcome": "DENY",
            "error_type": "WorkQueueError",
            "message": str(exc),
        }
    except PlatformAdapterError as exc:
        return {
            "outcome": "DENY",
            "error_type": "PlatformAdapterError",
            "message": str(exc),
        }
    except Exception:
        return {
            "outcome": "DENY",
            "error_type": "PlatformAuthorityUnavailable",
            "message": "Platform Authority request failed closed",
        }
