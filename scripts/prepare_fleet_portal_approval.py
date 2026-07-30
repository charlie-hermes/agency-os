#!/usr/bin/env python3
"""Create and bind one exact Paperclip decision packet for a confirmed fact."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agency_os.contracts import canonical_bytes, canonical_checksum
from agency_os.fleet_portal import FleetPortalAuthority, payload_checksum
from agency_os.integrations import (
    PaperclipBrandBinding,
    PaperclipHTTPTransport,
    PaperclipLifecycleAdapter,
)


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip().strip("'\"")
    return values


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(canonical_bytes(dict(value)) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare_candidate_approval(
    *,
    authority: FleetPortalAuthority,
    lifecycle: PaperclipLifecycleAdapter,
    candidate_id: str,
    checkpoint_path: Path,
    actor_id: str = "fleet_platform_administrator",
) -> dict[str, Any]:
    if not re.fullmatch(r"candidate_[A-Za-z0-9_-]{1,96}", candidate_id):
        raise ValueError("candidate identity is invalid")
    packet = authority.fleet_review_packet(actor_id=actor_id, candidate_id=candidate_id)
    if packet["brand_id"] != lifecycle.brand_id:
        raise RuntimeError("candidate and Paperclip brand bindings differ")
    campaign_id = f"fleet-portal-{candidate_id}"
    packet_checksum = packet["packet_checksum"]
    manifest = {
        key: value
        for key, value in packet.items()
        if key not in {"existing_approval_id", "packet_checksum"}
    }
    manifest.update({"campaign_id": campaign_id, "packet_checksum": packet_checksum})
    manifest_checksum = canonical_checksum(manifest)
    task = lifecycle.create_task(
        title=f"Review confirmed Brand Twin fact — {candidate_id}",
        campaign_id=campaign_id,
        stage="fleet_brand_fact_review",
        acceptance_criteria=(
            "The candidate statement matches the client-confirmed wording.",
            "The source, candidate and review checksums match the decision packet.",
            "Approve or reject this exact packet without changing its evidence.",
        ),
        status="in_review",
        idempotency_key=f"fleet-portal-task-{packet_checksum.removeprefix('sha256:')}",
        artifact_refs=(packet["source_checksum"], packet["candidate_checksum"],
                       packet["review_checksum"], packet_checksum),
    )
    if checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        if (
            checkpoint.get("candidate_id") != candidate_id
            or checkpoint.get("packet_checksum") != packet_checksum
            or checkpoint.get("manifest_checksum") != manifest_checksum
            or checkpoint.get("task_id") != task["id"]
        ):
            raise RuntimeError("approval checkpoint does not match current evidence")
        if checkpoint.get("stage") != "bound":
            raise RuntimeError(
                "Paperclip approval outcome is unknown; reconcile the saved task before retrying"
            )
        approval = lifecycle.get_approval(str(checkpoint["approval_id"]))
    else:
        if packet["existing_approval_id"] is not None:
            raise RuntimeError("candidate is bound but its local approval checkpoint is absent")
        intent = {
            "schema_version": "1.0", "stage": "requesting",
            "candidate_id": candidate_id, "packet_checksum": packet_checksum,
            "manifest_checksum": manifest_checksum, "task_id": task["id"],
        }
        atomic_json(checkpoint_path, intent)
        requested = lifecycle.request_approval(issue_ids=[task["id"]], manifest=manifest)
        approval = lifecycle.get_approval(str(requested["id"]))
        atomic_json(checkpoint_path, {
            **intent, "stage": "bound", "approval_id": approval["id"],
            "approval_snapshot_checksum": payload_checksum(approval),
        })
    if approval.get("payload") != manifest or approval.get("status") != "pending":
        raise RuntimeError("Paperclip approval readback is not the exact pending packet")
    approval_checksum = payload_checksum(approval)
    authority.bind_paperclip_approval(
        actor_id=actor_id, tenant_id=packet["tenant_id"],
        brand_id=packet["brand_id"], approval_id=str(approval["id"]),
        approval_checksum=approval_checksum, candidate_id=candidate_id,
    )
    return {
        "status": "pending_client_decision", "candidate_id": candidate_id,
        "paperclip_task_id": task["id"], "paperclip_approval_id": approval["id"],
        "approval_snapshot_checksum": approval_checksum,
        "packet_checksum": packet_checksum,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--database", type=Path, default=Path("/var/lib/agency-os/fleet-portal.sqlite3"))
    parser.add_argument("--credential-env", type=Path, default=Path("/etc/agency-os/fleet-command-paperclip.env"))
    parser.add_argument("--checkpoint-directory", type=Path, default=Path("/var/lib/agency-os/paperclip-approval-checkpoints"))
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("root required")
    values = load_env(args.credential_env)
    required = ("PAPERCLIP_BASE_URL", "PAPERCLIP_BOARD_TOKEN", "PAPERCLIP_COMPANY_ID", "FLEET_PORTAL_BRAND_ID")
    missing = [key for key in required if not values.get(key)]
    if missing:
        raise SystemExit("missing Paperclip worker configuration: " + ", ".join(missing))
    lifecycle = PaperclipLifecycleAdapter(
        PaperclipHTTPTransport(
            base_url=values["PAPERCLIP_BASE_URL"],
            bearer_token=values["PAPERCLIP_BOARD_TOKEN"],
        ),
        PaperclipBrandBinding(
            values["PAPERCLIP_COMPANY_ID"], values["FLEET_PORTAL_BRAND_ID"],
        ),
    )
    checkpoint = args.checkpoint_directory / f"{args.candidate_id}.json"
    result = prepare_candidate_approval(
        authority=FleetPortalAuthority(args.database), lifecycle=lifecycle,
        candidate_id=args.candidate_id, checkpoint_path=checkpoint,
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
