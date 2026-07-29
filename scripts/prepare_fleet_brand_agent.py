#!/usr/bin/env python3
"""Prepare exact Paperclip approval and entitlements for Fleet G2.5."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agency_os.brand_agent import load_brand_agent_policy
from agency_os.contracts import canonical_checksum
from agency_os.integrations import (
    PaperclipBoardApprovalAdapter,
    PaperclipBoardHTTPTransport,
    PaperclipBrandBinding,
    PaperclipHTTPTransport,
    PaperclipLifecycleAdapter,
)
from scripts.initialize_fleet_tenant import initialise as initialise_tenant


COMPANY_ID = "d7e2e389-c7ad-486e-87ca-482e4ec6216d"
PARENT_ISSUE_ID = "e262dc27-eb95-4c13-b8e2-64597b456ef6"


def load_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key] = value.strip().strip("'\"")
    return values


def atomic_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def activation_manifest(
    repository_root: Path,
    *,
    commit: str,
    company_id: str = COMPANY_ID,
) -> dict[str, Any]:
    if len(commit) != 40 or any(item not in "0123456789abcdef" for item in commit):
        raise ValueError("Agency OS commit is invalid")
    policy_path = repository_root / "config/fleet-brand-agent.json"
    runtime_path = repository_root / "config/fleet-brand-agent-runtime.json"
    acceptance_path = repository_root / "acceptance/fleet-brand-agent.json"
    policy = load_brand_agent_policy(policy_path)
    return {
        "schema_version": "1.0",
        "artifact_type": "brand_agent_activation_manifest",
        "campaign_id": "fleet-brand-agent-v1",
        "brand_id": policy.brand_id,
        "paperclip_company_id": company_id,
        "agency_os_commit": commit,
        "release_artifacts": [
            {
                "name": "brand_agent_policy",
                "checksum": "sha256:" + hashlib.sha256(policy_path.read_bytes()).hexdigest(),
            },
            {
                "name": "brand_agent_runtime_config",
                "checksum": "sha256:" + hashlib.sha256(runtime_path.read_bytes()).hexdigest(),
            },
            {
                "name": "brand_agent_acceptance_matrix",
                "checksum": "sha256:" + hashlib.sha256(acceptance_path.read_bytes()).hexdigest(),
            },
        ],
        "public_claim_ids": list(policy.public_claim_ids),
        "composer_version": policy.composer_version,
        "mcp_protocol_version": "2025-11-25",
        "transport": "authenticated_private_loopback",
        "transcript_default": "metadata_only",
        "transcript_content_requires_consent": True,
        "controlled_action": "request_human_follow_up",
        "action_confirmation_required": True,
        "action_reversible": True,
        "external_model": False,
        "provider_external_writes": False,
    }


def prepare_live(repository_root: Path) -> dict[str, Any]:
    if os.geteuid() != 0:
        raise PermissionError("root required")
    operator = load_env(Path("/etc/paperclip/operator.env"))
    lock = load_env(Path("/opt/paperclip/integration/build/appliance.lock"))
    token = operator.get("PAPERCLIP_BOARD_API_KEY", "")
    if not token:
        raise RuntimeError("Paperclip board credential is missing")
    company_id = Path("/etc/paperclip/company-id").read_text(encoding="utf-8").strip()
    if company_id != COMPANY_ID:
        raise RuntimeError("live Paperclip company is not Fleet DMA")
    commit = subprocess.run(
        ["git", "-C", str(repository_root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    if commit != lock.get("AGENCY_OS_COMMIT"):
        raise RuntimeError("Agency OS release differs from the appliance lock")
    manifest = activation_manifest(repository_root, commit=commit, company_id=company_id)
    tenant_config = json.loads(
        (repository_root / "config/fleet-generation2.json").read_text(encoding="utf-8")
    )
    tenancy = initialise_tenant(
        tenant_config, Path(tenant_config["authority_database"])
    )
    required_modules = {
        "content_engine", "brand_twin", "ai_market_observatory",
        "brand_agent", "controlled_actions",
    }
    if set(tenancy["enabled_modules"]) != required_modules:
        raise RuntimeError("Fleet G2.5 entitlements are incomplete")
    base_url = f"http://{lock['PAPERCLIP_GATEWAY']}:{lock['PAPERCLIP_PORT']}"
    binding = PaperclipBrandBinding(company_id, "brand_fleet")
    lifecycle = PaperclipLifecycleAdapter(
        PaperclipHTTPTransport(base_url=base_url, bearer_token=token), binding
    )
    task = lifecycle.create_task(
        title="Fleet Brand Agent v1 — activation and acceptance",
        campaign_id="fleet-brand-agent-v1",
        stage="activation",
        acceptance_criteria=(
            "Approved Fleet truth grounds every factual answer with citations.",
            "Unknown, protected, injection, and cross-tenant requests fail closed.",
            "Private web and MCP interfaces pass authentication and isolation checks.",
            "One confirmed Paperclip follow-up task is created and cancelled.",
            "No external model or provider write occurs.",
        ),
        parent_id=PARENT_ISSUE_ID,
        status="todo",
        idempotency_key=f"fleet-brand-agent-activation-{commit}",
        artifact_refs=(canonical_checksum(manifest),),
    )
    approval_path = (
        Path("/var/lib/agency-os/approvals")
        / f"fleet-brand-agent-{commit}.json"
    )
    if approval_path.is_file():
        approval = json.loads(approval_path.read_text(encoding="utf-8"))
        if (
            approval.get("status") != "approved"
            or approval.get("payload") != manifest
            or approval.get("issueIds") not in (None, [task["id"]])
        ):
            raise RuntimeError("stored Brand Agent activation approval changed")
    else:
        requested = lifecycle.request_approval(
            issue_ids=[task["id"]], manifest=manifest
        )
        board = PaperclipBoardApprovalAdapter(
            PaperclipBoardHTTPTransport(base_url=base_url, bearer_token=token),
            binding,
        )
        approval = board.decide_approval(
            str(requested["id"]),
            decision="approve",
            decision_note=(
                "Human owner approved the exact private Fleet Brand Agent v1 "
                "release and its one cancellable Paperclip-only action."
            ),
        )
        atomic_json(approval_path, approval)
    lifecycle.update_task(
        task["id"],
        status="todo",
        comment=(
            "Exact Brand Agent activation manifest approved. Awaiting private "
            "service and live G2.5 acceptance."
        ),
    )
    result = {
        "schema_version": "1.0",
        "status": "prepared",
        "brand_id": "brand_fleet",
        "company_id": company_id,
        "agency_os_commit": commit,
        "activation_task_id": task["id"],
        "approval_id": approval["id"],
        "manifest_checksum": canonical_checksum(manifest),
        "enabled_modules": tenancy["enabled_modules"],
        "external_model": False,
        "provider_external_writes": False,
    }
    atomic_json(
        Path("/var/lib/paperclip-appliance/fleet-brand-agent-prepared.json"), result
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    args = parser.parse_args()
    print(json.dumps(prepare_live(args.repository_root), sort_keys=True))


if __name__ == "__main__":
    main()
