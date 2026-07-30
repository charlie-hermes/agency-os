"""Unix-socket boundary between the Fleet web tier and protected authorities."""

from __future__ import annotations

import argparse
import json
import os
import socket
import socketserver
import struct
import threading
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from .fleet_portal import (
    FleetPortalAuthority,
    FleetPortalAuthorizationError,
    FleetPortalError,
)
from .fleet_tenancy import FleetTenantAuthority
from .store import Principal


MAXIMUM_REQUEST_BYTES = 64 * 1024
_CURRENT_WORKER_UIDS: frozenset[int] = frozenset()


class AuthorityHostError(RuntimeError):
    """The local authority service configuration or request is unsafe."""


class _AuthorityServer(socketserver.UnixStreamServer):
    allow_reuse_address = False

    def __init__(
        self,
        socket_path: str,
        authority: FleetPortalAuthority,
        allowed_uids: frozenset[int],
        worker_uids: frozenset[int],
    ) -> None:
        self.authority = authority
        self.allowed_uids = allowed_uids
        self.worker_uids = worker_uids
        super().__init__(socket_path, _AuthorityHandler)


class _AuthorityHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        peer = self.request.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        _pid, uid, _gid = struct.unpack("3i", peer)
        if uid not in self.server.allowed_uids:  # type: ignore[attr-defined]
            self._write({"ok": False, "error": "peer_not_authorized"})
            return
        raw = self.rfile.readline(MAXIMUM_REQUEST_BYTES + 1)
        if not raw or len(raw) > MAXIMUM_REQUEST_BYTES or not raw.endswith(b"\n"):
            self._write({"ok": False, "error": "request_size_or_framing"})
            return
        try:
            request = json.loads(raw.decode("utf-8"))
            if not isinstance(request, Mapping):
                raise AuthorityHostError("request must be an object")
            result = _dispatch(self.server.authority, request, uid)  # type: ignore[attr-defined]
            self._write({"ok": True, "result": result})
        except (json.JSONDecodeError, UnicodeDecodeError, AuthorityHostError, FleetPortalError,
                FleetPortalAuthorizationError, KeyError, TypeError, ValueError):
            self._write({"ok": False, "error": "request_denied"})

    def _write(self, value: Mapping[str, Any]) -> None:
        self.wfile.write(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n")


def _identity_context(authority: FleetPortalAuthority, request: Mapping[str, Any]):
    return authority.resolve_verified_identity(
        workos_subject=str(request["workos_subject"]),
        workos_organization_id=str(request["workos_organization_id"]),
        hostname=str(request["hostname"]), origin=str(request["origin"]),
        access_identity_verified=request.get("access_identity_verified") is True,
        session_id=str(request["session_id"]),
        correlation_id=str(request["correlation_id"]),
    )


def _dispatch(
    authority: FleetPortalAuthority, request: Mapping[str, Any], peer_uid: int = -1,
) -> Any:
    operation = request.get("operation")
    if operation == "health":
        return {"status": "pass", "authority": "fleet_portal", "schema_version": 3}
    if operation in {"claim_command", "transition_command", "materialize_outcome"}:
        # The server-supplied peer UID, never request JSON, defines this boundary.
        admitted_worker_uids = _CURRENT_WORKER_UIDS
        if peer_uid not in admitted_worker_uids:
            raise FleetPortalAuthorizationError("peer is not an admitted command worker")
        if operation == "claim_command":
            return authority.claim_next_command(
                worker_id=str(request["worker_id"]), brand_id=str(request["brand_id"]),
            )
        if operation == "transition_command":
            receipt = request.get("authority_receipt")
            if receipt is not None and not isinstance(receipt, Mapping):
                raise AuthorityHostError("authority receipt must be an object")
            authority.transition_command(
                worker_id=str(request["worker_id"]), tenant_id=str(request["tenant_id"]),
                brand_id=str(request["brand_id"]), command_id=str(request["command_id"]),
                expected_state=str(request["expected_state"]),
                next_state=str(request["next_state"]),
                authority_receipt=None if receipt is None else dict(receipt),
            )
            return {"status": "transitioned"}
        approval = request.get("approval")
        if not isinstance(approval, Mapping):
            raise AuthorityHostError("approval readback must be an object")
        return authority.materialize_approval_outcome(
            worker_id=str(request["worker_id"]),
            command_id=str(request["command_id"]), approval=dict(approval),
        )
    if operation == "accept_invitation":
        if request.get("access_identity_verified") is not True:
            raise FleetPortalAuthorizationError("verified edge identity is required")
        hostname = str(request["hostname"])
        if str(request["origin"]) != f"https://{hostname}":
            raise FleetPortalAuthorizationError("invitation origin is invalid")
        invitation = authority.pending_invitation_projection(
            invitation_id=str(request["invitation_id"]),
        )
        subject = str(request["workos_subject"])
        organization = str(request["workos_organization_id"])
        principal = Principal(subject, "platform-assurance-reviewer", invitation["brand_id"])
        if authority.tenant_authority_path is None:
            raise FleetPortalAuthorizationError("tenant authority is required")
        tenancy = FleetTenantAuthority(authority.tenant_authority_path)
        access = tenancy.portal_access_projection(
            principal, hostname, workos_organization_id=organization,
        )
        account = tenancy.account_brand_projection(principal)
        if access["tenant_id"] != invitation["tenant_id"]:
            raise FleetPortalAuthorizationError("invitation tenant binding changed")
        authority.accept_invitation(
            invitation_id=invitation["invitation_id"],
            invitation_token=str(request["invitation_token"]),
            invited_email=str(request["invited_email"]), verified_hostname=hostname,
            membership_id=str(request["membership_id"]), workos_subject=subject,
            workos_organization_id=organization,
            customer_account_id=account["customer_account_id"],
            client_brand_id=account["client_brand_id"],
            entitlement_version=int(access["entitlement_version"]),
        )
        return {"status": "accepted"}
    if operation == "admin_projection":
        subject = str(request.get("admin_subject", ""))
        admitted = {
            value for value in os.environ.get("FLEET_PORTAL_ADMIN_SUBJECTS", "").split(",")
            if value
        }
        if not admitted or subject not in admitted:
            raise FleetPortalAuthorizationError("Fleet administrator is not admitted")
        return authority.admin_projection(
            brand_id=os.environ.get("FLEET_PORTAL_PRODUCTION_BRAND_ID", "brand_fleet")
        )
    context = _identity_context(authority, request)
    if operation == "resolve_context":
        return asdict(context)
    if operation == "portal_projection":
        return authority.portal_projection(context)
    if operation == "list_content":
        return authority.list_content(context)
    if operation == "list_sources":
        return authority.list_sources(context)
    if operation == "list_candidates":
        return authority.list_candidates(context)
    if operation == "list_approvals":
        return authority.list_approvals(context)
    if operation == "list_memberships":
        return authority.list_memberships(context)
    if operation == "issue_invitation":
        if os.environ.get("FLEET_PORTAL_MUTATIONS_DISABLED") == "1":
            raise FleetPortalAuthorizationError("portal mutations are disabled")
        return authority.issue_invitation_for_context(
            context, invitation_id=str(request["invitation_id"]),
            invitation_token=str(request["invitation_token"]),
            email=str(request["email"]), client_role=str(request["client_role"]),
            approval_scopes=tuple(str(value) for value in request["approval_scopes"]),
        )
    if operation == "revoke_membership":
        if os.environ.get("FLEET_PORTAL_MUTATIONS_DISABLED") == "1":
            raise FleetPortalAuthorizationError("portal mutations are disabled")
        authority.revoke_membership_for_context(
            context, membership_id=str(request["membership_id"]),
        )
        return {"status": "revoked"}
    if operation == "reserve_source_upload":
        if os.environ.get("FLEET_PORTAL_MUTATIONS_DISABLED") == "1":
            raise FleetPortalAuthorizationError("portal mutations are disabled")
        return authority.reserve_source_upload(
            context, source_id=str(request["source_id"]),
            filename=str(request["filename"]), size_bytes=int(request["size_bytes"]),
            purpose=str(request["purpose"]),
        )
    if operation == "cancel_source_upload":
        authority.cancel_source_upload(context, source_id=str(request["source_id"]))
        return {"status": "cancelled"}
    if operation == "confirm_candidate":
        if os.environ.get("FLEET_PORTAL_MUTATIONS_DISABLED") == "1":
            raise FleetPortalAuthorizationError("portal mutations are disabled")
        return authority.confirm_candidate(
            context,
            candidate_id=str(request["candidate_id"]),
            expected_checksum=str(request["expected_checksum"]),
            statement=str(request["statement"]),
        )
    if operation == "submit_command":
        if os.environ.get("FLEET_PORTAL_MUTATIONS_DISABLED") == "1":
            raise FleetPortalAuthorizationError("portal mutations are disabled")
        command = request.get("command")
        if not isinstance(command, Mapping):
            raise AuthorityHostError("command must be an object")
        return authority.submit_command(
            context,
            command_id=str(command["command_id"]),
            idempotency_key=str(command["idempotency_key"]),
            command_type=str(command["command_type"]),
            target_id=str(command["target_id"]),
            expected_checksum=str(command["expected_checksum"]),
            approval_scope=str(command["approval_scope"]),
            payload=dict(command["payload"]),
        )
    raise AuthorityHostError("operation is not admitted")


def import_review_spool(authority: FleetPortalAuthority, review_root: Path) -> dict[str, int]:
    """Import complete scanner records into the protected authority."""

    admitted = review_root / "admitted"
    rejected = review_root / "authority-rejected"
    admitted.mkdir(mode=0o700, parents=True, exist_ok=True)
    rejected.mkdir(mode=0o700, parents=True, exist_ok=True)
    result = {"admitted": 0, "rejected": 0}
    for candidate in sorted(review_root.glob("*.review.json")):
        destination = admitted / candidate.name
        try:
            if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size > 12 * 1024 * 1024:
                raise AuthorityHostError("review artifact is unsafe")
            record = json.loads(candidate.read_text(encoding="utf-8"))
            if not isinstance(record, Mapping):
                raise AuthorityHostError("review artifact must be an object")
            authority.import_extraction(record)
        except (OSError, json.JSONDecodeError, AuthorityHostError, FleetPortalError):
            candidate.replace(rejected / candidate.name)
            result["rejected"] += 1
            continue
        candidate.replace(destination)
        result["admitted"] += 1
    authority.enforce_retention()
    return result


def _watch_review_spool(authority: FleetPortalAuthority, review_root: Path) -> None:
    while True:
        import_review_spool(authority, review_root)
        time.sleep(1.0)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--tenant-authority-database", type=Path, required=True)
    parser.add_argument("--brand-intelligence-database", type=Path, required=True)
    parser.add_argument("--review-spool", type=Path, required=True)
    parser.add_argument("--socket", type=Path, required=True)
    args = parser.parse_args()
    if not hasattr(socket, "SO_PEERCRED"):
        raise SystemExit("Fleet portal authority requires Linux SO_PEERCRED")
    allowed_text = os.environ.get("FLEET_AUTHORITY_ALLOWED_UIDS", "")
    try:
        allowed_uids = frozenset(int(value) for value in allowed_text.split(",") if value)
    except ValueError as exc:
        raise SystemExit("FLEET_AUTHORITY_ALLOWED_UIDS is invalid") from exc
    if not allowed_uids:
        raise SystemExit("FLEET_AUTHORITY_ALLOWED_UIDS must name admitted service UIDs")
    worker_text = os.environ.get("FLEET_AUTHORITY_WORKER_UIDS", "")
    try:
        worker_uids = frozenset(int(value) for value in worker_text.split(",") if value)
    except ValueError as exc:
        raise SystemExit("FLEET_AUTHORITY_WORKER_UIDS is invalid") from exc
    if not worker_uids or not worker_uids.issubset(allowed_uids):
        raise SystemExit("worker UIDs must be a non-empty subset of admitted UIDs")
    global _CURRENT_WORKER_UIDS
    _CURRENT_WORKER_UIDS = worker_uids
    try:
        socket_gid = int(os.environ["FLEET_AUTHORITY_SOCKET_GID"])
    except (KeyError, ValueError) as exc:
        raise SystemExit("FLEET_AUTHORITY_SOCKET_GID must name the portal service group") from exc
    if args.socket.exists():
        if not args.socket.is_socket():
            raise SystemExit("authority socket path is occupied by a non-socket")
        args.socket.unlink()
    args.socket.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    authority = FleetPortalAuthority(
        args.database,
        tenant_authority_path=args.tenant_authority_database,
        brand_intelligence_path=args.brand_intelligence_database,
    )
    import_review_spool(authority, args.review_spool)
    watcher = threading.Thread(
        target=_watch_review_spool, args=(authority, args.review_spool), daemon=True,
    )
    watcher.start()
    server = _AuthorityServer(str(args.socket), authority, allowed_uids, worker_uids)
    socket_uid = int(os.environ.get("FLEET_AUTHORITY_SOCKET_UID", "0"))
    os.chown(args.socket, socket_uid, socket_gid)
    os.chmod(args.socket, 0o660)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        if args.socket.exists() and args.socket.is_socket():
            args.socket.unlink()


if __name__ == "__main__":
    main()
