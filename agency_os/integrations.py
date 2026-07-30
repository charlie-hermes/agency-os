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
import re
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


class PaperclipBoardTransport(Protocol):
    def decide(
        self,
        approval_id: str,
        *,
        decision: str,
        decision_note: str,
        idempotency_key: str | None = None,
    ) -> Any: ...

    def get(self, approval_id: str) -> Any: ...


class BuzzTransport(Protocol):
    def run(self, arguments: Sequence[str]) -> Any: ...


def _require_mapping(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise IntegrationError(f"{label} response is invalid")
    return copy.deepcopy(dict(value))


def _require_brand(record: Mapping[str, Any], brand_id: str, label: str) -> None:
    observed = record.get("brand_id", record.get("brandId"))
    if observed != brand_id:
        raise IntegrationError(f"{label} crossed the tenant boundary")


def _require_company(record: Mapping[str, Any], company_id: str, label: str) -> None:
    observed = record.get("companyId", record.get("company_id"))
    if observed != company_id:
        raise IntegrationError(f"{label} crossed the company boundary")


def _require_uuid(value: Any, label: str) -> str:
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ContractError(f"{label} must be a UUID") from exc


def _task_metadata(record: Mapping[str, Any]) -> dict[str, Any]:
    description = record.get("description")
    if not isinstance(description, str) or "```json\n" not in description:
        raise IntegrationError("Paperclip task has no Agency OS metadata")
    encoded = description.split("```json\n", 1)[1].split("\n```", 1)[0]
    try:
        metadata = json.loads(encoded)
    except json.JSONDecodeError as exc:
        raise IntegrationError("Paperclip task metadata is invalid") from exc
    return _require_mapping(metadata, "Paperclip task metadata")


def _approval_binding(
    value: Any, binding: "PaperclipBrandBinding", label: str
) -> dict[str, Any]:
    approval = _require_mapping(value, label)
    _require_uuid(approval.get("id"), f"{label} identity")
    _require_company(approval, binding.company_id, label)
    payload = _require_mapping(approval.get("payload"), f"{label} payload")
    _require_brand(payload, binding.brand_id, label)
    return approval


@dataclass(frozen=True)
class PaperclipBrandBinding:
    """Immutable one-company-per-brand authority boundary."""

    company_id: str
    brand_id: str

    def __post_init__(self) -> None:
        canonical_company_id = _require_uuid(
            self.company_id, "Paperclip company_id"
        )
        object.__setattr__(self, "company_id", canonical_company_id)
        if not self.brand_id.startswith("brand_"):
            raise ValueError("Paperclip brand_id is invalid")


def _paperclip_lifecycle_route_admitted(method: str, path: str) -> bool:
    """Admit only exact canonical routes used by the lifecycle adapter."""

    if method == "GET" and path == "/api/health":
        return True
    parts = path.split("/")
    if (
        len(parts) < 4
        or parts[:2] != ["", "api"]
        or any(part in {"", ".", ".."} for part in parts[2:])
        or any("%" in part for part in parts)
    ):
        return False

    def canonical_uuid(index: int) -> bool:
        try:
            return str(UUID(parts[index])) == parts[index]
        except (ValueError, IndexError):
            return False

    if parts[2] == "companies" and len(parts) == 5 and canonical_uuid(3):
        return (method, parts[4]) in {
            ("GET", "issues"),
            ("POST", "issues"),
            ("POST", "approvals"),
            ("POST", "cost-events"),
        }
    if parts[2] == "issues" and canonical_uuid(3):
        if len(parts) == 4:
            return method in {"GET", "PATCH"}
        return (method, parts[4]) in {
            ("POST", "checkout"),
            ("POST", "release"),
            ("POST", "comments"),
        } and len(parts) == 5
    if parts[2] == "approvals" and canonical_uuid(3):
        return (len(parts) == 4 and method == "GET") or (
            len(parts) == 5 and method == "GET" and parts[4] == "issues"
        )
    return False


class PaperclipHTTPTransport:
    """Authenticated lifecycle transport with no unguarded sender method."""

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
        if not _paperclip_lifecycle_route_admitted(method, path):
            raise IntegrationError("Paperclip lifecycle route is not admitted")
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


class PaperclipBoardHTTPTransport:
    """Independent board credential exposing only exact approval decisions."""

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
            raise ValueError("Paperclip board bearer token is required")
        if timeout_seconds <= 0:
            raise ValueError("Paperclip timeout must be positive")
        self._base_url = base_url.rstrip("/")
        self._bearer_token = bearer_token
        self._timeout_seconds = float(timeout_seconds)
        self._opener = opener

    def decide(
        self,
        approval_id: str,
        *,
        decision: str,
        decision_note: str,
        idempotency_key: str | None = None,
    ) -> Any:
        if decision not in {"approve", "reject"} or not decision_note:
            raise ContractError("Paperclip approval decision is invalid")
        approval_id = _require_uuid(approval_id, "Paperclip approval_id")
        path = f"/api/approvals/{approval_id}/{decision}"
        body = canonical_bytes({"decisionNote": decision_note})
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._bearer_token}",
        }
        if idempotency_key is not None:
            if not re.fullmatch(r"[A-Za-z0-9._:-]{8,200}", idempotency_key):
                raise ContractError("Paperclip idempotency key is invalid")
            headers["Idempotency-Key"] = idempotency_key
        request = urllib.request.Request(
            f"{self._base_url}{path}", data=body, method="POST", headers=headers,
        )
        try:
            with self._opener(request, timeout=self._timeout_seconds) as response:
                raw = response.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            raise IntegrationError("Paperclip board request failed") from exc
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IntegrationError(
                "Paperclip board response is not valid JSON"
            ) from exc


    def get(self, approval_id: str) -> Any:
        approval_id = _require_uuid(approval_id, "Paperclip approval_id")
        request = urllib.request.Request(
            f"{self._base_url}/api/approvals/{approval_id}",
            method="GET",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self._bearer_token}",
            },
        )
        try:
            with self._opener(request, timeout=self._timeout_seconds) as response:
                raw = response.read()
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            raise IntegrationError("Paperclip board readback failed") from exc
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise IntegrationError("Paperclip board readback is not valid JSON") from exc


@dataclass(frozen=True)
class PaperclipLifecycleAdapter:
    """Exact installed task, approval, budget and closure route binding."""

    transport: PaperclipTransport
    binding: PaperclipBrandBinding

    @property
    def company_id(self) -> str:
        return self.binding.company_id

    @property
    def brand_id(self) -> str:
        return self.binding.brand_id

    _STATUSES = frozenset(
        {"backlog", "todo", "in_progress", "blocked", "in_review", "done", "cancelled"}
    )

    def create_task(
        self,
        *,
        title: str,
        campaign_id: str,
        stage: str,
        acceptance_criteria: Sequence[str],
        parent_id: str | None = None,
        blocked_by_issue_ids: Sequence[str] = (),
        status: str = "backlog",
        idempotency_key: str,
        artifact_refs: Sequence[str] = (),
    ) -> dict[str, Any]:
        if (
            not title
            or not isinstance(campaign_id, str)
            or not campaign_id
            or not stage
            or status not in self._STATUSES
        ):
            raise ContractError("Paperclip task input is invalid")
        metadata = {
            "schema_version": "1.0",
            "brand_id": self.brand_id,
            "campaign_id": campaign_id,
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
            payload["parentId"] = _require_uuid(parent_id, "Paperclip parent_id")
        if blocked_by_issue_ids:
            payload["blockedByIssueIds"] = [
                _require_uuid(item, "Paperclip blocker")
                for item in blocked_by_issue_ids
            ]
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
        tasks = []
        for item in response:
            task = _require_mapping(item, "Paperclip task")
            description = task.get("description")
            if not isinstance(description, str) or not description.startswith(
                "Agency OS authoritative task metadata:\n"
            ):
                continue
            if _task_metadata(task).get("brand_id") != self.brand_id:
                continue
            tasks.append(self._task(task))
        return tasks

    def get_task(self, issue_id: str) -> dict[str, Any]:
        issue_id = _require_uuid(issue_id, "Paperclip issue_id")
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
        issue_id = _require_uuid(issue_id, "Paperclip issue_id")
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
        issue_id = _require_uuid(issue_id, "Paperclip issue_id")
        agent_id = _require_uuid(agent_id, "Paperclip agent_id")
        return self._task(
            self.transport.request(
                "POST",
                f"/api/issues/{issue_id}/checkout",
                {"agentId": agent_id, "expectedStatuses": list(expected_statuses)},
            )
        )

    def release(self, issue_id: str) -> dict[str, Any]:
        issue_id = _require_uuid(issue_id, "Paperclip issue_id")
        return _require_mapping(
            self.transport.request("POST", f"/api/issues/{issue_id}/release", {}),
            "Paperclip release",
        )

    def comment(self, issue_id: str, body: str) -> dict[str, Any]:
        if not body:
            raise ContractError("Paperclip comment body is required")
        issue_id = _require_uuid(issue_id, "Paperclip issue_id")
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
        campaign_id = manifest.get("campaign_id")
        if (
            not issue_ids
            or manifest.get("brand_id") != self.brand_id
            or not isinstance(campaign_id, str)
            or not campaign_id
        ):
            raise ContractError("Paperclip approval tenant binding is invalid")
        validated_issue_ids = [
            _require_uuid(item, "Paperclip approval issue_id")
            for item in issue_ids
        ]
        for item in validated_issue_ids:
            task = self.get_task(item)
            if _task_metadata(task).get("campaign_id") != campaign_id:
                raise ContractError(
                    "Paperclip approval campaign binding is invalid"
                )
        payload = {
            "type": "request_board_approval",
            "issueIds": validated_issue_ids,
            "payload": copy.deepcopy(dict(manifest)),
        }
        if requested_by_agent_id is not None:
            payload["requestedByAgentId"] = _require_uuid(
                requested_by_agent_id, "Paperclip requester"
            )
        approval = _approval_binding(
            self.transport.request(
                "POST", f"/api/companies/{self.company_id}/approvals", payload
            ),
            self.binding,
            "Paperclip approval",
        )
        readback_ids = approval.get("issueIds")
        if readback_ids is None:
            approval["issueIds"] = list(validated_issue_ids)
        elif readback_ids != validated_issue_ids:
            raise IntegrationError("Paperclip approval issue binding changed")
        return approval

    def get_approval(self, approval_id: str) -> dict[str, Any]:
        approval_id = _require_uuid(approval_id, "Paperclip approval_id")
        return _approval_binding(
            self.transport.request("GET", f"/api/approvals/{approval_id}"),
            self.binding,
            "Paperclip approval",
        )

    def get_approval_issues(self, approval_id: str) -> list[dict[str, Any]]:
        approval = self.get_approval(approval_id)
        response = self.transport.request(
            "GET", f"/api/approvals/{approval_id}/issues"
        )
        if not isinstance(response, list):
            raise IntegrationError("Paperclip approval issue list is invalid")
        issues = [self._task(item) for item in response]
        readback_ids = approval.get("issueIds")
        if readback_ids is not None:
            if not isinstance(readback_ids, list):
                raise IntegrationError("Paperclip approval issue binding is invalid")
            expected = {
                _require_uuid(item, "Paperclip approval issue_id")
                for item in readback_ids
            }
            if {item["id"] for item in issues} != expected:
                raise IntegrationError("Paperclip approval issues changed")
        return issues

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
        _require_company(task, self.company_id, "Paperclip task")
        _require_uuid(task.get("id"), "Paperclip task identity")
        metadata = _task_metadata(task)
        _require_brand(metadata, self.brand_id, "Paperclip task")
        if task.get("status") not in self._STATUSES:
            raise IntegrationError("Paperclip task status is invalid")
        return task


@dataclass(frozen=True)
class PaperclipBoardApprovalAdapter:
    """Decision surface requiring a separately authenticated board transport."""

    transport: PaperclipBoardTransport
    binding: PaperclipBrandBinding

    def decide_approval(
        self,
        approval_id: str,
        *,
        decision: str,
        decision_note: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        if decision not in {"approve", "reject"} or not decision_note:
            raise ContractError("Paperclip approval decision is invalid")
        approval_id = _require_uuid(approval_id, "Paperclip approval_id")
        return _approval_binding(
            self.transport.decide(
                approval_id,
                decision=decision,
                decision_note=decision_note,
                idempotency_key=idempotency_key,
            ),
            self.binding,
            "Paperclip approval decision",
        )


    def get_approval(self, approval_id: str) -> dict[str, Any]:
        approval_id = _require_uuid(approval_id, "Paperclip approval_id")
        return _approval_binding(
            self.transport.get(approval_id), self.binding,
            "Paperclip approval readback",
        )


_BUZZ_ALLOWED_FLAGS = {
    ("channels", "create"): frozenset(
        {"--name", "--type", "--visibility", "--description", "--ttl"}
    ),
    ("channels", "get"): frozenset({"--channel"}),
    ("messages", "send"): frozenset(
        {"--channel", "--content", "--reply-to", "--file"}
    ),
    ("messages", "get"): frozenset(
        {"--channel", "--limit", "--before", "--since", "--kinds"}
    ),
}
_BUZZ_REQUIRED_FLAGS = {
    ("channels", "create"): frozenset(
        {"--name", "--type", "--visibility", "--description", "--ttl"}
    ),
    ("channels", "get"): frozenset({"--channel"}),
    ("messages", "send"): frozenset({"--channel", "--content"}),
    ("messages", "get"): frozenset({"--channel"}),
}


def _validate_buzz_arguments(arguments: Sequence[str]) -> list[str]:
    args = list(arguments)
    if len(args) < 2:
        raise IntegrationError("Buzz command is not admitted")
    command = (args[0], args[1])
    allowed = _BUZZ_ALLOWED_FLAGS.get(command)
    if allowed is None:
        raise IntegrationError("Buzz command is not admitted")
    tail = args[2:]
    if len(tail) % 2:
        raise IntegrationError("Buzz command arguments are invalid")
    flags = tail[::2]
    values = tail[1::2]
    if any(
        not isinstance(flag, str) or not flag.startswith("--") for flag in flags
    ):
        raise IntegrationError("Buzz command flags are invalid")
    if any(
        not isinstance(value, str) or value.startswith("-") for value in values
    ):
        raise IntegrationError("Buzz command values are invalid")
    if len(flags) != len(set(flags)):
        raise IntegrationError("Buzz command repeats a flag")
    observed = set(flags)
    if observed - allowed or not _BUZZ_REQUIRED_FLAGS[command] <= observed:
        raise IntegrationError("Buzz command flags are not admitted")
    return args


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
        if not relay_url.startswith(("http://", "https://", "wss://")):
            raise ValueError("Buzz relay URL is invalid")
        if not os.path.isabs(executable):
            raise ValueError("Buzz executable path must be absolute")
        self._relay_url = relay_url
        self._private_key_provider = private_key_provider
        self._auth_tag_provider = auth_tag_provider
        self._executable = executable
        self._timeout_seconds = timeout_seconds

    def run(self, arguments: Sequence[str]) -> Any:
        arguments = _validate_buzz_arguments(arguments)
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
        if channel.get("accepted") is False:
            raise IntegrationError("Buzz channel was not accepted")
        channel_id = channel.get("id", channel.get("channel_id"))
        if not isinstance(channel_id, str) or not channel_id:
            raise IntegrationError("Buzz channel identity is missing")
        channel["id"] = channel_id
        channel.setdefault("visibility", "private")
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
        message = _require_mapping(
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
        if message.get("accepted") is False:
            raise IntegrationError("Buzz message was not accepted")
        message_id = message.get("id", message.get("event_id"))
        if not isinstance(message_id, str) or not message_id:
            raise IntegrationError("Buzz message identity is missing")
        message["id"] = message_id
        return message
