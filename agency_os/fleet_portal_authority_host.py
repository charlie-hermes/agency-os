"""Unix-socket boundary between the Fleet web tier and protected authorities."""

from __future__ import annotations

import argparse
import json
import os
import socket
import socketserver
import struct
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from .fleet_portal import (
    FleetPortalAuthority,
    FleetPortalAuthorizationError,
    FleetPortalError,
)


MAXIMUM_REQUEST_BYTES = 64 * 1024


class AuthorityHostError(RuntimeError):
    """The local authority service configuration or request is unsafe."""


class _AuthorityServer(socketserver.UnixStreamServer):
    allow_reuse_address = False

    def __init__(
        self,
        socket_path: str,
        authority: FleetPortalAuthority,
        allowed_uids: frozenset[int],
    ) -> None:
        self.authority = authority
        self.allowed_uids = allowed_uids
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
            result = _dispatch(self.server.authority, request)  # type: ignore[attr-defined]
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


def _dispatch(authority: FleetPortalAuthority, request: Mapping[str, Any]) -> Any:
    operation = request.get("operation")
    if operation == "health":
        return {"status": "pass", "authority": "fleet_portal", "schema_version": 1}
    context = _identity_context(authority, request)
    if operation == "resolve_context":
        return asdict(context)
    if operation == "list_content":
        return authority.list_content(context)
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
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
        raise SystemExit("FLEET_AUTHORITY_ALLOWED_UIDS must name the web and worker UIDs")
    try:
        socket_gid = int(os.environ["FLEET_AUTHORITY_SOCKET_GID"])
    except (KeyError, ValueError) as exc:
        raise SystemExit("FLEET_AUTHORITY_SOCKET_GID must name the portal service group") from exc
    if args.socket.exists():
        if not args.socket.is_socket():
            raise SystemExit("authority socket path is occupied by a non-socket")
        args.socket.unlink()
    args.socket.parent.mkdir(mode=0o750, parents=True, exist_ok=True)
    server = _AuthorityServer(str(args.socket), FleetPortalAuthority(args.database), allowed_uids)
    os.chown(args.socket, 0, socket_gid)
    os.chmod(args.socket, 0o660)
    try:
        server.serve_forever(poll_interval=0.25)
    finally:
        server.server_close()
        if args.socket.exists() and args.socket.is_socket():
            args.socket.unlink()


if __name__ == "__main__":
    main()
