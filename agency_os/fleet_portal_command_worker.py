"""Restricted G2.6 worker that records portal decisions in Paperclip."""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path
from typing import Any, Mapping

from .fleet_portal import FleetPortalAuthority, FleetPortalError
from .integrations import (
    IntegrationError,
    PaperclipBoardApprovalAdapter,
    PaperclipBoardHTTPTransport,
    PaperclipBrandBinding,
)


class FleetPortalWorkerError(RuntimeError):
    """A portal command cannot safely be dispatched."""


def process_one(
    authority: FleetPortalAuthority,
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
    try:
        if command["command_type"] != "paperclip_approval_decision":
            authority.transition_command(
                worker_id=worker_id, tenant_id=tenant_id, brand_id=brand_id,
                command_id=command_id, expected_state="dispatching", next_state="rejected",
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
        approval = board.decide_approval(
            command["target_id"], decision=decision, decision_note=decision_note,
        )
        receipt = {
            "authority": "Paperclip",
            "paperclip_approval_id": approval["id"],
            "paperclip_status": approval["status"],
            "payload_checksum": command["payload_checksum"],
        }
        authority.transition_command(
            worker_id=worker_id, tenant_id=tenant_id, brand_id=brand_id,
            command_id=command_id, expected_state="dispatching",
            next_state="authority_recorded", authority_receipt=receipt,
        )
        authority.transition_command(
            worker_id=worker_id, tenant_id=tenant_id, brand_id=brand_id,
            command_id=command_id, expected_state="authority_recorded",
            next_state="projecting",
        )
        authority.transition_command(
            worker_id=worker_id, tenant_id=tenant_id, brand_id=brand_id,
            command_id=command_id, expected_state="projecting", next_state="completed",
        )
        return {"command_id": command_id, "state": "completed", "receipt": receipt}
    except (FleetPortalWorkerError, IntegrationError, FleetPortalError, KeyError, TypeError):
        authority.transition_command(
            worker_id=worker_id, tenant_id=tenant_id, brand_id=brand_id,
            command_id=command_id, expected_state="dispatching", next_state="unknown",
            authority_receipt={"reason": "paperclip_outcome_requires_reconciliation"},
        )
        return {"command_id": command_id, "state": "unknown"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
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
    authority = FleetPortalAuthority(args.database)
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
