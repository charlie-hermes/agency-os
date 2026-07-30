"""Restricted G2.6 worker that records portal decisions in Paperclip."""

from __future__ import annotations

import argparse
import json
import os
import socket
import time
from pathlib import Path
from typing import Any, Mapping

from .fleet_portal import FleetPortalError, payload_checksum
from .integrations import (
    IntegrationError,
    PaperclipBoardApprovalAdapter,
    PaperclipBoardHTTPTransport,
    PaperclipBrandBinding,
)


class FleetPortalWorkerError(RuntimeError):
    """A portal command cannot safely be dispatched."""


class FleetPortalAuthorityClient:
    """UID-authenticated command-worker client for the local authority socket."""

    def __init__(self, socket_path: Path, *, timeout_seconds: float = 2.0) -> None:
        if not socket_path.is_absolute() or not str(socket_path).startswith("/run/agency-os/"):
            raise FleetPortalWorkerError("authority socket path is unsafe")
        self.socket_path = socket_path
        self.timeout_seconds = timeout_seconds

    def _call(self, request: Mapping[str, Any]) -> Any:
        encoded = json.dumps(dict(request), sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        if len(encoded) > 64 * 1024:
            raise FleetPortalWorkerError("authority request is too large")
        response = bytearray()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(self.timeout_seconds)
                client.connect(str(self.socket_path))
                client.sendall(encoded)
                client.shutdown(socket.SHUT_WR)
                while True:
                    chunk = client.recv(65536)
                    if not chunk:
                        break
                    response.extend(chunk)
                    if len(response) > 128 * 1024:
                        raise FleetPortalWorkerError("authority response is too large")
        except (OSError, TimeoutError) as exc:
            raise FleetPortalWorkerError("authority socket call failed") from exc
        try:
            value = json.loads(response.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FleetPortalWorkerError("authority response is invalid") from exc
        if not isinstance(value, Mapping) or value.get("ok") is not True:
            raise FleetPortalWorkerError("authority denied the worker operation")
        return value.get("result")

    def claim_next_command(self, *, worker_id: str, brand_id: str) -> dict[str, Any] | None:
        result = self._call({
            "operation": "claim_command", "worker_id": worker_id, "brand_id": brand_id,
        })
        return None if result is None else dict(result)

    def transition_command(self, **values: Any) -> None:
        self._call({"operation": "transition_command", **values})

    def materialize_approval_outcome(
        self, *, worker_id: str, command_id: str, approval: Mapping[str, Any],
    ) -> dict[str, Any]:
        return dict(self._call({
            "operation": "materialize_outcome", "worker_id": worker_id,
            "command_id": command_id, "approval": dict(approval),
        }))


def process_one(
    authority: Any,
    board: PaperclipBoardApprovalAdapter,
    *,
    worker_id: str,
    brand_id: str,
) -> dict[str, Any] | None:
    command = authority.claim_next_command(worker_id=worker_id, brand_id=brand_id)
    if command is None:
        return None
    command_id = command["command_id"]
    tenant_id = command["tenant_id"]
    current_state = "dispatching"
    try:
        if command["command_type"] != "paperclip_approval_decision":
            authority.transition_command(
                worker_id=worker_id, tenant_id=tenant_id, brand_id=brand_id,
                command_id=command_id, expected_state=current_state, next_state="rejected",
                authority_receipt={"reason": "command_type_not_admitted"},
            )
            return {"command_id": command_id, "state": "rejected"}
        payload = command["payload"]
        if not isinstance(payload, Mapping):
            raise FleetPortalWorkerError("command payload is invalid")
        decision = payload.get("decision")
        decision_note = payload.get("decision_note")
        if decision not in {"approve", "reject"} or not isinstance(decision_note, str):
            raise FleetPortalWorkerError("Paperclip decision payload is invalid")
        desired_status = "approved" if decision == "approve" else "rejected"
        observed = board.get_approval(command["target_id"])
        observed_status = observed.get("status")
        if observed_status not in {"approved", "rejected"}:
            if payload_checksum(observed) != command["expected_checksum"]:
                authority.transition_command(
                    worker_id=worker_id, tenant_id=tenant_id, brand_id=brand_id,
                    command_id=command_id, expected_state=current_state,
                    next_state="conflict",
                    authority_receipt={"reason": "paperclip_precondition_changed"},
                )
                return {"command_id": command_id, "state": "conflict"}
            board.decide_approval(
                command["target_id"], decision=decision,
                decision_note=decision_note,
                idempotency_key=command["idempotency_key"],
            )
            observed = board.get_approval(command["target_id"])
            observed_status = observed.get("status")
        if observed_status != desired_status:
            authority.transition_command(
                worker_id=worker_id, tenant_id=tenant_id, brand_id=brand_id,
                command_id=command_id, expected_state=current_state,
                next_state="conflict",
                authority_receipt={"reason": "paperclip_decision_conflict"},
            )
            return {"command_id": command_id, "state": "conflict"}
        receipt = {
            "authority": "Paperclip",
            "paperclip_approval_id": observed["id"],
            "paperclip_status": observed_status,
            "paperclip_readback_checksum": payload_checksum(observed),
            "payload_checksum": command["payload_checksum"],
            "idempotency_key": command["idempotency_key"],
        }
        authority.transition_command(
            worker_id=worker_id, tenant_id=tenant_id, brand_id=brand_id,
            command_id=command_id, expected_state=current_state,
            next_state="authority_recorded", authority_receipt=receipt,
        )
        current_state = "authority_recorded"
        authority.transition_command(
            worker_id=worker_id, tenant_id=tenant_id, brand_id=brand_id,
            command_id=command_id, expected_state=current_state,
            next_state="projecting",
        )
        current_state = "projecting"
        projection = authority.materialize_approval_outcome(
            worker_id=worker_id, command_id=command_id, approval=observed,
        )
        authority.transition_command(
            worker_id=worker_id, tenant_id=tenant_id, brand_id=brand_id,
            command_id=command_id, expected_state=current_state, next_state="completed",
            authority_receipt={**receipt, "projection": projection},
        )
        return {
            "command_id": command_id, "state": "completed",
            "receipt": receipt, "projection": projection,
        }
    except (FleetPortalWorkerError, IntegrationError, FleetPortalError, KeyError, TypeError):
        if current_state in {"dispatching", "authority_recorded", "projecting"}:
            authority.transition_command(
                worker_id=worker_id, tenant_id=tenant_id, brand_id=brand_id,
                command_id=command_id, expected_state=current_state, next_state="unknown",
                authority_receipt={"reason": "paperclip_outcome_requires_reconciliation"},
            )
        return {"command_id": command_id, "state": "unknown"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    args = parser.parse_args()
    required = {
        "PAPERCLIP_BASE_URL": os.environ.get("PAPERCLIP_BASE_URL"),
        "PAPERCLIP_BOARD_TOKEN": (
            os.environ.get("PAPERCLIP_BOARD_TOKEN")
            or os.environ.get("PAPERCLIP_BOARD_API_KEY")
        ),
        "PAPERCLIP_COMPANY_ID": os.environ.get("PAPERCLIP_COMPANY_ID"),
        "FLEET_PORTAL_BRAND_ID": os.environ.get("FLEET_PORTAL_BRAND_ID", "brand_fleet"),
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise SystemExit("missing worker environment: " + ", ".join(sorted(missing)))
    authority = FleetPortalAuthorityClient(args.socket)
    board = PaperclipBoardApprovalAdapter(
        PaperclipBoardHTTPTransport(
            base_url=str(required["PAPERCLIP_BASE_URL"]),
            bearer_token=str(required["PAPERCLIP_BOARD_TOKEN"]),
        ),
        PaperclipBrandBinding(
            company_id=str(required["PAPERCLIP_COMPANY_ID"]),
            brand_id=str(required["FLEET_PORTAL_BRAND_ID"]),
        ),
    )
    while True:
        result = process_one(
            authority, board, worker_id="fleet-command-worker",
            brand_id=str(required["FLEET_PORTAL_BRAND_ID"]),
        )
        if args.once:
            return
        if result is None:
            time.sleep(max(args.poll_seconds, 0.1))


if __name__ == "__main__":
    main()
