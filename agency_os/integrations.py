"""Typed bindings for the admitted Paperclip and Buzz lifecycle surfaces.

The concrete transports hold credentials outside request artifacts.  Tests use
injected transports, so the complete contract can be exercised without changing
the installed services or performing a real external write.
"""

from __future__ import annotations

import copy
import ipaddress
import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence
from uuid import UUID

from .contracts import ContractError, canonical_bytes, parse_time


class IntegrationError(RuntimeError):
    """An admitted platform integration failed safely."""


class PaperclipTransport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
    ) -> Any: ...


class BuzzTransport(Protocol):
    def run(self, arguments: Sequence[str]) -> Any: ...


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise IntegrationError(f"{label} response is invalid")
    return copy.deepcopy(dict(value))


def _require_brand(record: Mapping[str, Any], brand_id: str, label: str) -> None:
    observed = record.get("brand_id", record.get("brandId"))
    if observed is not None and observed != brand_id:
        raise IntegrationError(f"{label} crossed the tenant boundary")


def _require_company(record: Mapping[str, Any], company_id: str, label: str) -> None:
    observed = record.get("companyId", record.get("company_id"))
    if observed is not None and observed != company_id:
        raise IntegrationError(f"{label} crossed the company boundary")


class PaperclipHTTPTransport:
    """Authenticated JSON transport for the pinned private Paperclip API."""

    def __init__(
        self,
        *,
        base_url: str,
        bearer_token: str,
        timeout_seconds: float = 10.0,
        opener: Callable[..., Any] = urllib.request.urlopen,
    ) -> None:
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("Paperclip base URL is invalid")
        if parsed.scheme == "http":
            try:
                address = ipaddress.ip_address(parsed.hostname)
            except ValueError as exc:
                raise ValueError(
                    "plaintext Paperclip URL must use a private IP"
                ) from exc
            if not (address.is_private or address.is_loopback):
                raise ValueError("plaintext Paperclip URL must remain private")
        if not isinstance(bearer_token, str) or not bearer_token:
            raise ValueError("Paperclip bearer token is required")
        if timeout_seconds <= 0:
            raise ValueError("Paperclip timeout must be positive")
        self._base_url = base_url.rstrip("/")
        self._bearer_token = bearer_token
        self._timeout_seconds = float(timeout_seconds)
        self._opener = opener

    def request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
    ) -> Any:
        if method not in {"GET", "POST", "PATCH"} or not path.startswith("/api/"):
            raise IntegrationError("Paperclip operation is not admitted")
        body = None if payload is None else canonical_bytes(dict(payload))
        request = urllib.request.Request(
            f"{self._base_url}{path}",
            data=body,
            method=method,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self._bearer_token}",
            },
        )
        try:
            with self._opener(request, timeout=self._timeout_seconds) as response:
                raw = response.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            raise IntegrationError("Paperclip request failed") from exc
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IntegrationError("Paperclip response is not valid JSON") from exc


@dataclass(frozen=True)
class PaperclipLifecycleAdapter:
    """Exact installed task, approval, budget and closure route binding."""

    transport: PaperclipTransport
    company_id: str
    brand_id: str

    _STATUSES = frozenset(
        {"backlog", "todo", "in_progress", "blocked", "in_review", "done", "cancelled"}
    )

    def __post_init__(self) -> None:
        if not self.company_id or not self.brand_id:
            raise ValueError("Paperclip company_id and brand_id are required")

    def create_task(
        self,
        *,
        title: str,
        stage: str,
        acceptance_criteria: Sequence[str],
        parent_id: str | None = None,
        blocked_by_issue_ids: Sequence[str] = (),
        status: str = "backlog",
        idempotency_key: str,
        artifact_refs: Sequence[str] = (),
    ) -> dict[str, Any]:
        if not title or not stage or status not in self._STATUSES:
            raise ContractError("Paperclip task input is invalid")
        metadata = {
            "schema_version": "1.0",
            "brand_id": self.brand_id,
            "stage": stage,
            "acceptance_criteria": list(acceptance_criteria),
            "artifact_refs": list(artifact_refs),
        }
        payload: dict[str, Any] = {
            "title": title,
            "description": (
                "Agency OS authoritative task metadata:\n"
                f"```json\n{canonical_bytes(metadata).decode('utf-8')}\n```"
            ),
            "status": status,
            "priority": "medium",
            "workMode": "standard",
            "idempotencyKey": idempotency_key,
            "allowDuplicate": False,
        }
        if parent_id is not None:
            payload["parentId"] = parent_id
        if blocked_by_issue_ids:
            payload["blockedByIssueIds"] = list(blocked_by_issue_ids)
        return self._task(
            self.transport.request(
                "POST", f"/api/companies/{self.company_id}/issues", payload
            )
        )

    def list_tasks(self) -> list[dict[str, Any]]:
        response = self.transport.request(
            "GET", f"/api/companies/{self.company_id}/issues"
        )
        if not isinstance(response, list):
            raise IntegrationError("Paperclip task list response is invalid")
        return [self._task(item) for item in response]

    def get_task(self, issue_id: str) -> dict[str, Any]:
        return self._task(
            self.transport.request("GET", f"/api/issues/{issue_id}")
        )

    def update_task(
        self,
        issue_id: str,
        *,
        status: str,
        comment: str,
    ) -> dict[str, Any]:
        if status not in self._STATUSES or not comment:
            raise ContractError("Paperclip task transition is invalid")
        return self._task(
            self.transport.request(
                "PATCH",
                f"/api/issues/{issue_id}",
                {"status": status, "comment": comment},
            )
        )

    def checkout(
        self,
        issue_id: str,
        *,
        agent_id: str,
        expected_statuses: Sequence[str],
    ) -> dict[str, Any]:
        if not agent_id or not expected_statuses:
            raise ContractError("Paperclip checkout binding is invalid")
        return self._task(
            self.transport.request(
                "POST",
                f"/api/issues/{issue_id}/checkout",
                {"agentId": agent_id, "expectedStatuses": list(expected_statuses)},
            )
        )

    def release(self, issue_id: str) -> dict[str, Any]:
        return _require_mapping(
            self.transport.request("POST", f"/api/issues/{issue_id}/release", {}),
            "Paperclip release",
        )

    def comment(self, issue_id: str, body: str) -> dict[str, Any]:
        if not body:
            raise ContractError("Paperclip comment body is required")
        return _require_mapping(
            self.transport.request(
                "POST", f"/api/issues/{issue_id}/comments", {"body": body}
            ),
            "Paperclip comment",
        )

    def request_approval(
        self,
        *,
        issue_ids: Sequence[str],
        manifest: Mapping[str, Any],
        requested_by_agent_id: str | None = None,
    ) -> dict[str, Any]:
        if not issue_ids or manifest.get("brand_id") != self.brand_id:
            raise ContractError("Paperclip approval tenant binding is invalid")
        payload = {
            "type": "request_board_approval",
            "issueIds": list(issue_ids),
            "payload": copy.deepcopy(dict(manifest)),
        }
        if requested_by_agent_id is not None:
            payload["requestedByAgentId"] = requested_by_agent_id
        approval = _require_mapping(
            self.transport.request(
                "POST", f"/api/companies/{self.company_id}/approvals", payload
            ),
            "Paperclip approval",
        )
        _require_brand(approval, self.brand_id, "Paperclip approval")
        _require_company(approval, self.company_id, "Paperclip approval")
        return approval

    def get_approval(self, approval_id: str) -> dict[str, Any]:
        approval = _require_mapping(
            self.transport.request("GET", f"/api/approvals/{approval_id}"),
            "Paperclip approval",
        )
        _require_brand(approval, self.brand_id, "Paperclip approval")
        _require_company(approval, self.company_id, "Paperclip approval")
        return approval

    def get_approval_issues(self, approval_id: str) -> list[dict[str, Any]]:
        response = self.transport.request(
            "GET", f"/api/approvals/{approval_id}/issues"
        )
        if not isinstance(response, list):
            raise IntegrationError("Paperclip approval issue list is invalid")
        return [self._task(item) for item in response]

    def decide_approval(
        self,
        approval_id: str,
        *,
        decision: str,
        decision_note: str,
        human_authority: bool,
    ) -> dict[str, Any]:
        if not human_authority:
            raise PermissionError("Paperclip approval decision requires human authority")
        if decision not in {"approve", "reject"} or not decision_note:
            raise ContractError("Paperclip approval decision is invalid")
        approval = _require_mapping(
            self.transport.request(
                "POST",
                f"/api/approvals/{approval_id}/{decision}",
                {"decisionNote": decision_note},
            ),
            "Paperclip approval decision",
        )
        _require_brand(approval, self.brand_id, "Paperclip approval")
        _require_company(approval, self.company_id, "Paperclip approval")
        return approval

    def record_cost(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        body = copy.deepcopy(dict(payload))
        allowed = {
            "agentId", "issueId", "projectId", "goalId", "heartbeatRunId",
            "billingCode", "provider", "biller", "billingType", "costStatus",
            "model", "inputTokens", "cachedInputTokens", "outputTokens",
            "costCents", "occurredAt",
        }
        required = {"agentId", "provider", "model", "costCents", "occurredAt"}
        if not required.issubset(body) or set(body) - allowed:
            raise ContractError("Paperclip cost event fields are invalid")
        try:
            UUID(str(body["agentId"]))
            for field in ("issueId", "projectId", "goalId", "heartbeatRunId"):
                if body.get(field) is not None:
                    UUID(str(body[field]))
            parse_time(body["occurredAt"])
        except (ValueError, TypeError) as exc:
            raise ContractError(
                "Paperclip cost event identity or time is invalid"
            ) from exc
        if not all(
            isinstance(body[field], str) and body[field]
            for field in ("provider", "model")
        ):
            raise ContractError("Paperclip cost provider and model are required")
        for field in (
            "costCents", "inputTokens", "cachedInputTokens", "outputTokens"
        ):
            value = body.get(field, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ContractError(
                    "Paperclip cost values must be non-negative integers"
                )
        return _require_mapping(
            self.transport.request(
                "POST", f"/api/companies/{self.company_id}/cost-events", body
            ),
            "Paperclip cost event",
        )

    def _task(self, value: Any) -> dict[str, Any]:
        task = _require_mapping(value, "Paperclip task")
        _require_brand(task, self.brand_id, "Paperclip task")
        _require_company(task, self.company_id, "Paperclip task")
        if not isinstance(task.get("id"), str) or not task["id"]:
            raise IntegrationError("Paperclip task identity is invalid")
        if task.get("status") not in self._STATUSES:
            raise IntegrationError("Paperclip task status is invalid")
        return task


class BuzzCliTransport:
    """Secret-minimising transport for the installed Buzz JSON CLI."""

    def __init__(
        self,
        *,
        relay_url: str,
        private_key_provider: Callable[[], str],
        auth_tag_provider: Callable[[], str | None] | None = None,
        executable: str = "/usr/local/bin/buzz",
        timeout_seconds: float = 10.0,
    ) -> None:
        if not relay_url.startswith(("http://", "https://")):
            raise ValueError("Buzz relay URL is invalid")
        if not os.path.isabs(executable):
            raise ValueError("Buzz executable path must be absolute")
        self._relay_url = relay_url
        self._private_key_provider = private_key_provider
        self._auth_tag_provider = auth_tag_provider
        self._executable = executable
        self._timeout_seconds = timeout_seconds

    def run(self, arguments: Sequence[str]) -> Any:
        if "--broadcast" in arguments:
            raise IntegrationError("Buzz broadcast is denied")
        key = self._private_key_provider()
        if not isinstance(key, str) or not key:
            raise IntegrationError("Buzz identity is unavailable")
        env = os.environ.copy()
        env["BUZZ_PRIVATE_KEY"] = key
        auth_tag = self._auth_tag_provider() if self._auth_tag_provider else None
        if auth_tag:
            env["BUZZ_AUTH_TAG"] = auth_tag
        try:
            completed = subprocess.run(
                [
                    self._executable,
                    "--relay",
                    self._relay_url,
                    "--format",
                    "json",
                    *arguments,
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=self._timeout_seconds,
                env=env,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise IntegrationError("Buzz command failed") from exc
        if completed.returncode != 0:
            raise IntegrationError("Buzz command failed")
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise IntegrationError("Buzz response is not valid JSON") from exc


@dataclass(frozen=True)
class TypedBuzzAdapter:
    """Private, bounded collaboration adapter; never a task-state authority."""

    transport: BuzzTransport
    brand_id: str

    def create_context_channel(
        self,
        *,
        campaign_id: str,
        purpose: str,
        ttl_seconds: int = 3600,
    ) -> dict[str, Any]:
        if not campaign_id or not purpose or ttl_seconds <= 0:
            raise ContractError("Buzz context channel input is invalid")
        channel = _require_mapping(
            self.transport.run(
                [
                    "channels",
                    "create",
                    "--name",
                    f"{self.brand_id}-{campaign_id}",
                    "--type",
                    "forum",
                    "--visibility",
                    "private",
                    "--description",
                    purpose,
                    "--ttl",
                    str(ttl_seconds),
                ]
            ),
            "Buzz channel",
        )
        if channel.get("visibility") not in {None, "private"}:
            raise IntegrationError("Buzz channel is not private")
        return channel

    def post_context(
        self,
        channel_id: str,
        context_packet: Mapping[str, Any],
    ) -> dict[str, Any]:
        if context_packet.get("brand_id") != self.brand_id:
            raise ContractError("Buzz context crossed the tenant boundary")
        return self._post(channel_id, {"kind": "context", **dict(context_packet)})

    def post_decision(
        self,
        channel_id: str,
        *,
        paperclip_issue_id: str,
        decision: str,
        evidence_refs: Sequence[str],
    ) -> dict[str, Any]:
        if not decision or not paperclip_issue_id:
            raise ContractError("Buzz decision is invalid")
        return self._post(
            channel_id,
            {
                "kind": "decision_summary",
                "brand_id": self.brand_id,
                "paperclip_issue_id": paperclip_issue_id,
                "decision": decision,
                "evidence_refs": list(evidence_refs),
                "authority": "non_authoritative_until_written_to_paperclip",
            },
        )

    def messages(self, channel_id: str, *, limit: int = 50) -> list[dict[str, Any]]:
        response = self.transport.run(
            ["messages", "get", "--channel", channel_id, "--limit", str(limit)]
        )
        if not isinstance(response, list):
            raise IntegrationError("Buzz message list response is invalid")
        return [copy.deepcopy(dict(item)) for item in response if isinstance(item, Mapping)]

    def _post(self, channel_id: str, content: Mapping[str, Any]) -> dict[str, Any]:
        if not channel_id:
            raise ContractError("Buzz channel identity is required")
        return _require_mapping(
            self.transport.run(
                [
                    "messages",
                    "send",
                    "--channel",
                    channel_id,
                    "--content",
                    canonical_bytes(dict(content)).decode("utf-8"),
                ]
            ),
            "Buzz message",
        )
