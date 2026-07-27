"""Protected local host and worker-only IPC client for fictional dispatch.

Only the independently started host owns the gateway, identity catalogue,
credential broker, publisher, and assertion signer. Worker processes receive an
``ActionGatewayClient`` containing only a Unix-socket path.
"""

from __future__ import annotations

import copy
import json
import multiprocessing
import os
import socket
import struct
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from .capabilities import CapabilityRegistry
from .contracts import canonical_bytes
from .gateway import _AuthorityActionGateway, GatewayDenied, MockPublisher
from .ledger import ActionLedger
from .runtime_security import (
    FictionalCredentialBroker,
    RuntimeIdentityAuthority,
    RuntimeIdentityError,
    RuntimeObservation,
    _observe_linux_process,
    _observe_linux_process_by_pid,
    _read_socket_line,
)
from .store import Principal, TenantStore


@dataclass(frozen=True)
class _RuntimeEnrollment:
    """Authority-created exact OS identity to principal mapping."""

    observation: RuntimeObservation
    principal: Principal


def _authority_enrollment_for_process(
    pid: int,
    principal: Principal,
    *,
    runtime_id: str,
) -> _RuntimeEnrollment:
    """Observe an already-running worker from the authority side."""

    if not all((principal.actor_id, principal.role_id, principal.brand_id)):
        raise ValueError("runtime principal fields must be non-empty")
    return _RuntimeEnrollment(
        observation=_observe_linux_process_by_pid(pid, runtime_id),
        principal=principal,
    )


class ActionGatewayClient:
    """Worker-side IPC client with no gateway, broker, signer, or catalogue."""

    __slots__ = ("_socket_path",)

    def __init__(self, socket_path: str) -> None:
        if not socket_path:
            raise ValueError("gateway host socket path is required")
        self._socket_path = socket_path

    def publish(
        self,
        *,
        manifest: Mapping[str, Any],
        approval_id: str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        request = {
            "operation": "publish",
            "manifest": copy.deepcopy(dict(manifest)),
            "approval_id": approval_id,
            "idempotency_key": idempotency_key,
        }
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(3)
                client.connect(self._socket_path)
                client.sendall(canonical_bytes(request) + b"\n")
                response = json.loads(_read_socket_line(client))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise GatewayDenied("GATEWAY_HOST_UNAVAILABLE") from exc
        if not isinstance(response, dict):
            raise GatewayDenied("GATEWAY_HOST_RESPONSE_INVALID")
        if response.get("outcome") != "ALLOW":
            raise GatewayDenied(str(response.get("code", "GATEWAY_HOST_DENIED")))
        receipt = response.get("receipt")
        if not isinstance(receipt, dict):
            raise GatewayDenied("GATEWAY_HOST_RESPONSE_INVALID")
        return copy.deepcopy(receipt)


def fictional_runtime(socket_path: str) -> ActionGatewayClient:
    """Create only a worker IPC client for an already-provisioned host."""

    return ActionGatewayClient(socket_path)


_AUTHORITY_HOST_TOKEN = object()


class _AuthorityGatewayHost:
    """Authority-side lifecycle handle; never passed to a worker."""

    def __init__(
        self,
        *,
        enrollment: _RuntimeEnrollment,
        capability_id: str,
        capability_registry: CapabilityRegistry,
        credential_broker: FictionalCredentialBroker,
        publisher: MockPublisher,
        approval_store: TenantStore,
        approval_authorities: Mapping[
            str, Mapping[str, list[str] | tuple[str, ...]]
        ],
        action_ledger: ActionLedger,
        clock: Callable[[], datetime] | None,
        _construction_token: object,
    ) -> None:
        if _construction_token is not _AUTHORITY_HOST_TOKEN:
            raise RuntimeIdentityError("AUTHORITY_HOST_CONSTRUCTION_DENIED")
        self._temporary_directory = tempfile.TemporaryDirectory(
            prefix="agency-os-gateway-host-"
        )
        self.socket_path = str(
            Path(self._temporary_directory.name) / "gateway.sock"
        )
        context = multiprocessing.get_context("fork")
        self._control_parent, control_child = context.Pipe(duplex=True)
        self._process = context.Process(
            target=_run_authority_gateway_host,
            args=(
                self.socket_path,
                enrollment,
                capability_id,
                capability_registry,
                credential_broker,
                publisher,
                approval_store,
                copy.deepcopy(dict(approval_authorities)),
                action_ledger,
                clock,
                control_child,
            ),
            daemon=True,
        )
        self._closed = False
        self._process.start()
        control_child.close()
        if not self._control_parent.poll(3):
            self.close()
            raise RuntimeIdentityError("AUTHORITY_HOST_UNAVAILABLE")
        outcome = self._control_parent.recv()
        if outcome != "READY":
            self.close()
            raise RuntimeIdentityError(str(outcome))

    def client(self) -> ActionGatewayClient:
        if self._closed:
            raise RuntimeIdentityError("AUTHORITY_HOST_UNAVAILABLE")
        return ActionGatewayClient(self.socket_path)

    def snapshot(self) -> dict[str, Any]:
        if self._closed or not self._process.is_alive():
            raise RuntimeIdentityError("AUTHORITY_HOST_UNAVAILABLE")
        self._control_parent.send({"operation": "snapshot"})
        if not self._control_parent.poll(3):
            raise RuntimeIdentityError("AUTHORITY_HOST_UNAVAILABLE")
        snapshot = self._control_parent.recv()
        if not isinstance(snapshot, dict):
            raise RuntimeIdentityError("AUTHORITY_HOST_UNAVAILABLE")
        return copy.deepcopy(snapshot)

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
        self._closed = True

    def __enter__(self) -> "_AuthorityGatewayHost":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def _provision_authority_gateway_host(
    *,
    enrollment: _RuntimeEnrollment,
    capability_id: str,
    capability_registry: CapabilityRegistry,
    credential_broker: FictionalCredentialBroker,
    publisher: MockPublisher,
    approval_store: TenantStore,
    approval_authorities: Mapping[
        str, Mapping[str, list[str] | tuple[str, ...]]
    ],
    action_ledger: ActionLedger,
    clock: Callable[[], datetime] | None = None,
) -> _AuthorityGatewayHost:
    """Authority bootstrap only: start the protected host for one enrollment."""

    return _AuthorityGatewayHost(
        enrollment=enrollment,
        capability_id=capability_id,
        capability_registry=capability_registry,
        credential_broker=credential_broker,
        publisher=publisher,
        approval_store=approval_store,
        approval_authorities=approval_authorities,
        action_ledger=action_ledger,
        clock=clock,
        _construction_token=_AUTHORITY_HOST_TOKEN,
    )


def _run_authority_gateway_host(
    socket_path: str,
    enrollment: _RuntimeEnrollment,
    capability_id: str,
    capability_registry: CapabilityRegistry,
    credential_broker: FictionalCredentialBroker,
    publisher: MockPublisher,
    approval_store: TenantStore,
    approval_authorities: Mapping[
        str, Mapping[str, list[str] | tuple[str, ...]]
    ],
    action_ledger: ActionLedger,
    clock: Callable[[], datetime] | None,
    control: Any,
) -> None:
    authority = RuntimeIdentityAuthority()
    authority.enroll(enrollment.observation, enrollment.principal)
    gateway = _AuthorityActionGateway(
        capability_id=capability_id,
        capability_registry=capability_registry,
        credential_broker=credential_broker,
        publisher=publisher,
        approval_store=approval_store,
        approval_authorities=approval_authorities,
        action_ledger=action_ledger,
        clock=clock,
    )
    authoritative_receipts: list[dict[str, Any]] = []
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(socket_path)
            os.chmod(socket_path, 0o600)
            server.listen(8)
            server.settimeout(0.1)
            control.send("READY")
            while True:
                if control.poll():
                    command = control.recv()
                    if command == {"operation": "snapshot"}:
                        control.send(
                            {
                                "publisher_calls": publisher.calls,
                                "publisher_objects": copy.deepcopy(publisher.objects),
                                "credential_audit": copy.deepcopy(
                                    credential_broker.audit
                                ),
                                "gateway_audit": copy.deepcopy(gateway.audit),
                                "authoritative_receipts": copy.deepcopy(
                                    authoritative_receipts
                                ),
                            }
                        )
                    elif command == {"operation": "shutdown"}:
                        control.send("CLOSED")
                        return
                    else:
                        control.send({"code": "AUTHORITY_CONTROL_INVALID"})
                try:
                    connection, _ = server.accept()
                except TimeoutError:
                    continue
                with connection:
                    response = _handle_worker_request(
                        connection, authority, enrollment, gateway
                    )
                    if response.get("outcome") == "ALLOW" and isinstance(
                        response.get("receipt"), dict
                    ):
                        authoritative_receipts.append(
                            copy.deepcopy(response["receipt"])
                        )
                    connection.sendall(canonical_bytes(response) + b"\n")
    except Exception:
        try:
            control.send("AUTHORITY_HOST_UNAVAILABLE")
        except (BrokenPipeError, EOFError, OSError):
            pass
    finally:
        control.close()


def _handle_worker_request(
    connection: socket.socket,
    authority: RuntimeIdentityAuthority,
    enrollment: _RuntimeEnrollment,
    gateway: _AuthorityActionGateway,
) -> dict[str, Any]:
    try:
        request = json.loads(_read_socket_line(connection))
        peer_pid, peer_uid, _ = struct.unpack(
            "3i",
            connection.getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")
            ),
        )
        observation = _observe_linux_process(
            peer_pid, peer_uid, enrollment.observation.runtime_id
        )
        now = datetime.now(timezone.utc)
        assertion = authority.issue_assertion(observation, now=now)
        principal = authority.authenticate(assertion, observation, now=now)
        if not isinstance(request, dict) or set(request) != {
            "operation",
            "manifest",
            "approval_id",
            "idempotency_key",
        }:
            raise RuntimeIdentityError("GATEWAY_REQUEST_INVALID")
        if request.get("operation") != "publish":
            raise RuntimeIdentityError("GATEWAY_REQUEST_INVALID")
        if not isinstance(request.get("manifest"), dict) or not isinstance(
            request.get("approval_id"), str
        ) or not isinstance(request.get("idempotency_key"), str):
            raise RuntimeIdentityError("GATEWAY_REQUEST_INVALID")
        receipt = gateway.dispatch_authorized(
            principal=principal,
            manifest=request["manifest"],
            approval_id=request["approval_id"],
            idempotency_key=request["idempotency_key"],
        )
        return {"outcome": "ALLOW", "receipt": receipt}
    except RuntimeIdentityError as exc:
        return {"outcome": "DENY", "code": exc.code}
    except GatewayDenied as exc:
        return {"outcome": "DENY", "code": str(exc)}
    except Exception:
        return {"outcome": "DENY", "code": "GATEWAY_HOST_UNAVAILABLE"}
