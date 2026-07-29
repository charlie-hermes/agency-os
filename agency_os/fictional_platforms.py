"""Deterministic installed-surface doubles for the complete Core proof.

These transports implement the exact paths and command shapes consumed by the
real adapters. They never contact installed services or perform external writes.
"""

from __future__ import annotations

import copy
import json
from typing import Any, Mapping, Sequence
from uuid import UUID

from .integrations import IntegrationError


class InMemoryPaperclipTransport:
    """Small route-faithful Paperclip authority used by acceptance tests."""

    def __init__(self, *, company_id: str, brand_id: str) -> None:
        self.company_id = company_id
        self.brand_id = brand_id
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.issues: dict[str, dict[str, Any]] = {}
        self.approvals: dict[str, dict[str, Any]] = {}
        self.comments: dict[str, list[dict[str, Any]]] = {}
        self.cost_events: list[dict[str, Any]] = []
        self._issue_sequence = 100
        self._approval_sequence = 1

    def request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
    ) -> Any:
        body = None if payload is None else copy.deepcopy(dict(payload))
        self.calls.append((method, path, body))
        company_prefix = f"/api/companies/{self.company_id}"

        if method == "POST" and path == f"{company_prefix}/issues":
            assert body is not None
            for item in [body.get("parentId"), *body.get("blockedByIssueIds", [])]:
                if item is not None:
                    self._require_uuid(item, "Paperclip issue relation")
            issue_id = f"00000000-0000-4000-8000-{self._issue_sequence:012x}"
            self._issue_sequence += 1
            issue = {
                "id": issue_id,
                "companyId": self.company_id,
                "brand_id": self.brand_id,
                "title": body["title"],
                "description": body["description"],
                "status": body["status"],
                "priority": body["priority"],
                "workMode": body["workMode"],
                "parentId": body.get("parentId"),
                "blockedByIssueIds": list(body.get("blockedByIssueIds", [])),
                "assigneeAgentId": None,
                "version": 1,
            }
            self.issues[issue_id] = issue
            return copy.deepcopy(issue)

        if method == "GET" and path == f"{company_prefix}/issues":
            return [copy.deepcopy(item) for item in self.issues.values()]

        if method == "POST" and path == f"{company_prefix}/approvals":
            assert body is not None
            if body.get("type") != "request_board_approval":
                raise IntegrationError("Paperclip approval type is not admitted")
            for item in body.get("issueIds", []):
                self._require_uuid(item, "Paperclip approval issue")
                self._issue(item)
            if body.get("requestedByAgentId") is not None:
                self._require_uuid(body["requestedByAgentId"], "Paperclip requester")
            approval_id = f"00000000-0000-4000-8000-{1000 + self._approval_sequence:012x}"
            self._approval_sequence += 1
            approval = {
                "id": approval_id,
                "companyId": self.company_id,
                "brand_id": self.brand_id,
                "type": body["type"],
                "status": "pending",
                "issueIds": list(body["issueIds"]),
                "payload": copy.deepcopy(body["payload"]),
                "requestedByAgentId": body.get("requestedByAgentId"),
            }
            self.approvals[approval_id] = approval
            return copy.deepcopy(approval)

        if method == "POST" and path == f"{company_prefix}/cost-events":
            assert body is not None
            event = {"id": f"00000000-0000-4000-8000-{2000 + len(self.cost_events):012x}", **body}
            self.cost_events.append(event)
            return copy.deepcopy(event)

        parts = path.removeprefix("/api/").split("/")
        if len(parts) >= 2 and parts[0] == "issues":
            issue = self._issue(parts[1])
            if len(parts) == 2 and method == "GET":
                return copy.deepcopy(issue)
            if len(parts) == 2 and method == "PATCH":
                assert body is not None
                if "status" in body:
                    if (
                        body["status"] == "in_progress"
                        and issue["assigneeAgentId"] is None
                    ):
                        raise IntegrationError(
                            "Paperclip in_progress tasks require an assignee"
                        )
                    issue["status"] = body["status"]
                issue["version"] += 1
                if body.get("comment"):
                    self._comment(issue["id"], body["comment"])
                return copy.deepcopy(issue)
            if len(parts) == 3 and parts[2] == "checkout" and method == "POST":
                assert body is not None
                if issue["status"] not in body["expectedStatuses"]:
                    raise IntegrationError("Paperclip checkout status changed")
                self._require_uuid(body.get("agentId"), "Paperclip agent")
                issue["assigneeAgentId"] = body["agentId"]
                issue["status"] = "in_progress"
                issue["version"] += 1
                return copy.deepcopy(issue)
            if len(parts) == 3 and parts[2] == "release" and method == "POST":
                issue["assigneeAgentId"] = None
                issue["version"] += 1
                return {"issueId": issue["id"], "released": True}
            if len(parts) == 3 and parts[2] == "comments" and method == "POST":
                assert body is not None
                return self._comment(issue["id"], body["body"])

        if len(parts) == 3 and parts[0] == "approvals" and parts[2] == "issues" and method == "GET":
            approval = self._approval(parts[1])
            return [
                copy.deepcopy(self._issue(issue_id))
                for issue_id in approval["issueIds"]
            ]

        if len(parts) >= 2 and parts[0] == "approvals":
            approval = self._approval(parts[1])
            if len(parts) == 2 and method == "GET":
                return copy.deepcopy(approval)
            if len(parts) == 3 and method == "POST" and parts[2] in {"approve", "reject"}:
                assert body is not None
                approval["status"] = "approved" if parts[2] == "approve" else "rejected"
                approval["decisionNote"] = body["decisionNote"]
                return copy.deepcopy(approval)

        raise IntegrationError("Paperclip route is not implemented by the proof")

    @staticmethod
    def _require_uuid(value: Any, label: str) -> None:
        try:
            UUID(str(value))
        except (ValueError, TypeError, AttributeError) as exc:
            raise IntegrationError(f"{label} must be a UUID") from exc

    def _issue(self, issue_id: str) -> dict[str, Any]:
        self._require_uuid(issue_id, "Paperclip issue")
        try:
            return self.issues[issue_id]
        except KeyError as exc:
            raise IntegrationError("Paperclip issue was not found") from exc

    def _approval(self, approval_id: str) -> dict[str, Any]:
        self._require_uuid(approval_id, "Paperclip approval")
        try:
            return self.approvals[approval_id]
        except KeyError as exc:
            raise IntegrationError("Paperclip approval was not found") from exc

    def _comment(self, issue_id: str, body: str) -> dict[str, Any]:
        comment = {
            "id": f"00000000-0000-4000-8000-{3000 + sum(map(len, self.comments.values())):012x}",
            "issueId": issue_id,
            "body": body,
        }
        self.comments.setdefault(issue_id, []).append(comment)
        return copy.deepcopy(comment)


class InMemoryPaperclipBoardTransport:
    """Separate board session exposing approval decisions only."""

    def __init__(self, authority: InMemoryPaperclipTransport) -> None:
        self._authority = authority
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

    def decide(
        self,
        approval_id: str,
        *,
        decision: str,
        decision_note: str,
    ) -> Any:
        if decision not in {"approve", "reject"} or not decision_note:
            raise IntegrationError("Paperclip board operation is not admitted")
        path = f"/api/approvals/{approval_id}/{decision}"
        body = {"decisionNote": decision_note}
        self.calls.append(("POST", path, body))
        return self._authority.request("POST", path, body)


class InMemoryBuzzTransport:
    """Private-channel Buzz command double with an inspectable transcript."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.channels: dict[str, dict[str, Any]] = {}
        self.channel_messages: dict[str, list[dict[str, Any]]] = {}

    def run(self, arguments: Sequence[str]) -> Any:
        args = list(arguments)
        self.calls.append(args)
        if "--broadcast" in args:
            raise IntegrationError("Buzz broadcast is denied")
        if args[:2] == ["channels", "create"]:
            visibility = self._value(args, "--visibility")
            if visibility != "private":
                raise IntegrationError("Buzz proof permits private channels only")
            channel_id = f"buzz_{len(self.channels) + 1}"
            channel = {
                "id": channel_id,
                "name": self._value(args, "--name"),
                "type": self._value(args, "--type"),
                "visibility": visibility,
                "description": self._value(args, "--description"),
                "ttl": int(self._value(args, "--ttl")),
            }
            self.channels[channel_id] = channel
            self.channel_messages[channel_id] = []
            return copy.deepcopy(channel)
        if args[:2] == ["messages", "send"]:
            channel_id = self._value(args, "--channel")
            if channel_id not in self.channels:
                raise IntegrationError("Buzz channel was not found")
            content = json.loads(self._value(args, "--content"))
            message = {
                "id": f"message_{len(self.channel_messages[channel_id]) + 1}",
                "channel_id": channel_id,
                "content": content,
            }
            self.channel_messages[channel_id].append(message)
            return copy.deepcopy(message)
        if args[:2] == ["messages", "get"]:
            channel_id = self._value(args, "--channel")
            limit = int(self._value(args, "--limit"))
            return copy.deepcopy(self.channel_messages.get(channel_id, [])[-limit:])
        raise IntegrationError("Buzz command is not implemented by the proof")

    @staticmethod
    def _value(arguments: Sequence[str], flag: str) -> str:
        try:
            return arguments[arguments.index(flag) + 1]
        except (ValueError, IndexError) as exc:
            raise IntegrationError(f"Buzz command is missing {flag}") from exc
