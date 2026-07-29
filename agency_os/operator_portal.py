"""Read-only operator portal over authoritative Paperclip task state."""

from __future__ import annotations

import argparse
import html
import json
import os
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping, Sequence

from .integrations import (
    PaperclipBrandBinding,
    PaperclipHTTPTransport,
    PaperclipLifecycleAdapter,
)
from .operator_view import _metadata
from .provider_handoffs import load_provider_catalog
from .runtime_bundles import ALL_RUNTIME_ROLES, verify_bundle_catalog


class OperatorPortalError(ValueError):
    """The operator portal configuration is incomplete or unsafe."""


def build_operator_views(
    adapters: Sequence[PaperclipLifecycleAdapter],
    *,
    approval_ids: Mapping[str, Sequence[str]] | None = None,
    provider_catalog: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build every operator view without mutating Paperclip."""

    approval_ids = approval_ids or {}
    provider_catalog = provider_catalog or load_provider_catalog()
    brands: list[dict[str, Any]] = []
    campaigns: list[dict[str, Any]] = []
    approvals: list[dict[str, Any]] = []
    calendar: list[dict[str, Any]] = []
    performance: list[dict[str, Any]] = []
    portfolio_counts: Counter[str] = Counter()
    for adapter in adapters:
        tasks = adapter.list_tasks()
        task_rows: list[dict[str, Any]] = []
        campaign_groups: dict[str, list[dict[str, Any]]] = {}
        for task in tasks:
            metadata = _metadata(task.get("description"))
            campaign_id = metadata.get("campaign_id")
            if not campaign_id:
                raise OperatorPortalError("Paperclip task has no campaign identity")
            row = {
                "paperclip_issue_id": task["id"],
                "title": task.get("title"),
                "campaign_id": campaign_id,
                "stage": metadata.get("stage"),
                "status": task["status"],
                "blocked_by": list(task.get("blockedByIssueIds", [])),
                "artifact_refs": list(metadata.get("artifact_refs", [])),
            }
            task_rows.append(row)
            campaign_groups.setdefault(campaign_id, []).append(row)
            portfolio_counts[task["status"]] += 1
            calendar.append(
                {
                    "brand_id": adapter.brand_id,
                    "campaign_id": campaign_id,
                    "paperclip_issue_id": task["id"],
                    "title": task.get("title"),
                    "schedule": metadata.get("schedule_window", "unscheduled"),
                    "status": task["status"],
                }
            )
            if metadata.get("stage") in {"measurement_learning", "social_measurement"}:
                performance.append(
                    {
                        "brand_id": adapter.brand_id,
                        "campaign_id": campaign_id,
                        "paperclip_issue_id": task["id"],
                        "status": task["status"],
                        "evidence": list(metadata.get("artifact_refs", [])),
                    }
                )
        brands.append(
            {
                "brand_id": adapter.brand_id,
                "company_id": adapter.company_id,
                "task_counts": dict(Counter(row["status"] for row in task_rows)),
                "tasks": task_rows,
            }
        )
        for campaign_id, rows in campaign_groups.items():
            campaigns.append(
                {
                    "brand_id": adapter.brand_id,
                    "campaign_id": campaign_id,
                    "task_counts": dict(Counter(row["status"] for row in rows)),
                    "stages": [row["stage"] for row in rows],
                    "tasks": rows,
                }
            )
        for approval_id in approval_ids.get(adapter.brand_id, ()):
            approval = adapter.get_approval(approval_id)
            issues = adapter.get_approval_issues(approval_id)
            approvals.append(
                {
                    "brand_id": adapter.brand_id,
                    "approval_id": approval["id"],
                    "status": approval["status"],
                    "issue_ids": [item["id"] for item in issues],
                    "manifest_checksum": approval.get("payload", {}).get("content_checksum"),
                }
            )
    bundle_evidence = verify_bundle_catalog()
    return {
        "schema_version": "1.0",
        "authority": "paperclip",
        "projection": "read_only",
        "portfolio": {
            "brand_count": len(brands),
            "campaign_count": len(campaigns),
            "task_counts": dict(portfolio_counts),
        },
        "brands": brands,
        "campaigns": campaigns,
        "approvals": approvals,
        "calendar": calendar,
        "performance": performance,
        "admin": {
            "runtime": bundle_evidence["target_runtime"],
            "roles": list(ALL_RUNTIME_ROLES),
            "role_bundle_count": bundle_evidence["bundle_count"],
            "provider_policy": provider_catalog.get("policy"),
            "providers": list(provider_catalog.get("providers", [])),
        },
    }


def load_live_adapters(path: Path) -> tuple[list[PaperclipLifecycleAdapter], dict[str, list[str]]]:
    config = json.loads(path.read_text())
    base_url = config.get("paperclip_base_url")
    bindings = config.get("bindings")
    if not isinstance(base_url, str) or not isinstance(bindings, list) or not bindings:
        raise OperatorPortalError("operator portal configuration is incomplete")
    adapters: list[PaperclipLifecycleAdapter] = []
    approval_ids: dict[str, list[str]] = {}
    for item in bindings:
        if not isinstance(item, dict) or "bearer_token" in item:
            raise OperatorPortalError("operator portal tokens must come from the environment")
        token_env = item.get("token_env")
        token = os.environ.get(token_env, "") if isinstance(token_env, str) else ""
        if not token:
            raise OperatorPortalError("operator portal token environment variable is missing")
        binding = PaperclipBrandBinding(item.get("company_id", ""), item.get("brand_id", ""))
        adapters.append(
            PaperclipLifecycleAdapter(
                PaperclipHTTPTransport(base_url=base_url, bearer_token=token),
                binding,
            )
        )
        approval_ids[binding.brand_id] = list(item.get("approval_ids", []))
    return adapters, approval_ids


def _serve(snapshot: dict[str, Any], host: str, port: int) -> None:
    if host not in {"127.0.0.1", "::1"}:
        raise OperatorPortalError("operator portal must bind to loopback")
    encoded = json.dumps(snapshot, indent=2, sort_keys=True)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path == "/api/status":
                body = encoded.encode()
                content_type = "application/json"
            elif self.path == "/":
                body = (
                    "<!doctype html><html><head><title>Agency OS</title>"
                    "<meta charset='utf-8'><style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:0 20px}"
                    "pre{white-space:pre-wrap;background:#f4f4f5;padding:20px;border-radius:10px}</style></head>"
                    "<body><h1>Agency OS operator status</h1><p>Read-only view. Paperclip remains authoritative.</p>"
                    f"<pre>{html.escape(encoded)}</pre></body></html>"
                ).encode()
                content_type = "text/html; charset=utf-8"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: Any) -> None:
            return

    ThreadingHTTPServer((host, port), Handler).serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only Agency OS operator portal")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3180)
    args = parser.parse_args()
    adapters, approvals = load_live_adapters(args.config)
    snapshot = build_operator_views(adapters, approval_ids=approvals)
    if args.serve:
        _serve(snapshot, args.host, args.port)
    else:
        print(json.dumps(snapshot, sort_keys=True))


if __name__ == "__main__":
    main()
