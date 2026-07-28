"""Read-only operator projection over authoritative Paperclip work."""

from __future__ import annotations

import json
from typing import Any, Sequence

from .integrations import IntegrationError, PaperclipLifecycleAdapter


def _metadata(description: Any) -> dict[str, Any]:
    if not isinstance(description, str) or "```json\n" not in description:
        return {}
    encoded = description.split("```json\n", 1)[1].split("\n```", 1)[0]
    try:
        value = json.loads(encoded)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def build_campaign_projection(
    paperclip: PaperclipLifecycleAdapter,
    *,
    campaign_id: str,
    approval_ids: Sequence[str] = (),
    task_ids: Sequence[str],
) -> dict[str, Any]:
    """Project campaign state without creating a second source of truth."""

    if not isinstance(campaign_id, str) or not campaign_id:
        raise IntegrationError("Campaign projection requires campaign_id")
    known_task_ids = set(task_ids)
    tasks = [paperclip.get_task(item) for item in task_ids]
    approvals = [
        (
            paperclip.get_approval(item),
            paperclip.get_approval_issues(item),
        )
        for item in approval_ids
    ]
    status_counts: dict[str, int] = {}
    for approval, approval_issues in approvals:
        if approval.get("payload", {}).get("campaign_id") != campaign_id:
            raise IntegrationError(
                "Paperclip approval escaped the campaign boundary"
            )
        if not {item["id"] for item in approval_issues} <= known_task_ids:
            raise IntegrationError(
                "Paperclip approval escaped the campaign task boundary"
            )
    projected_tasks = []
    for task in tasks:
        metadata = _metadata(task.get("description"))
        if metadata.get("campaign_id") != campaign_id:
            raise IntegrationError(
                "Paperclip task escaped the campaign boundary"
            )
        status_counts[task["status"]] = status_counts.get(task["status"], 0) + 1
        projected_tasks.append(
            {
                "paperclip_issue_id": task["id"],
                "title": task.get("title"),
                "stage": metadata.get("stage"),
                "status": task["status"],
                "blocked_by": list(task.get("blockedByIssueIds", [])),
                "artifact_refs": list(metadata.get("artifact_refs", [])),
            }
        )
    return {
        "schema_version": "1.0",
        "projection": "read_only",
        "authority": "paperclip",
        "brand_id": paperclip.brand_id,
        "campaign_id": campaign_id,
        "task_counts": status_counts,
        "tasks": projected_tasks,
        "approvals": [
            {
                "approval_id": approval["id"],
                "status": approval["status"],
                "issue_ids": [issue["id"] for issue in approval_issues],
                "manifest_checksum": approval.get("payload", {}).get("content_checksum"),
            }
            for approval, approval_issues in approvals
        ],
    }
