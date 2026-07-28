"""Read-only operator projection over authoritative Paperclip work."""

from __future__ import annotations

import json
from typing import Any, Sequence

from .integrations import PaperclipLifecycleAdapter


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
    approval_ids: Sequence[str] = (),
) -> dict[str, Any]:
    """Project campaign state without creating a second source of truth."""

    tasks = paperclip.list_tasks()
    approvals = [
        (
            paperclip.get_approval(item),
            paperclip.get_approval_issues(item),
        )
        for item in approval_ids
    ]
    status_counts: dict[str, int] = {}
    projected_tasks = []
    for task in tasks:
        status_counts[task["status"]] = status_counts.get(task["status"], 0) + 1
        metadata = _metadata(task.get("description"))
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
