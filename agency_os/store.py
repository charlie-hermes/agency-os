"""Tenant-scoped in-memory store and role authorization."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from .contracts import ContractError, canonical_bytes, parse_time, verify_record


class AuthorizationError(PermissionError):
    """The principal is outside a tenant or role boundary."""


@dataclass(frozen=True)
class Principal:
    actor_id: str
    role_id: str
    brand_id: str


ROLE_WRITES: dict[str, frozenset[str]] = {
    "agency-director": frozenset(
        {"publication_manifest", "learning_context_manifest", "learning_record"}
    ),
    "brand-brief-steward": frozenset({"brand_profile", "campaign_brief"}),
    "search-content-strategist": frozenset({"research_pack", "content_brief"}),
    "content-producer": frozenset({"draft_asset_package", "failure_observation"}),
    "search-answer-optimiser": frozenset(
        {"complete_asset_package", "failure_observation"}
    ),
    "editorial-integrity-qa": frozenset(
        {"qa_verdict", "qa_passed_asset_package", "failure_observation"}
    ),
    "publishing-operator": frozenset(
        {"publication_receipt", "failure_observation"}
    ),
    "growth-intelligence-analyst": frozenset(
        {"performance_snapshot", "candidate_learning", "failure_observation"}
    ),
    "human-approver": frozenset({"approval_record"}),
}


class TenantStore:
    def __init__(self) -> None:
        self._records: dict[str, dict[str, dict[str, Any]]] = {}
        self.audit: list[dict[str, str]] = []

    @staticmethod
    def _record_id(record: Mapping[str, Any]) -> str:
        for key in (
            "manifest_id",
            "approval_id",
            "receipt_id",
            "learning_record_id",
            "context_manifest_id",
            "failure_observation_id",
            "candidate_learning_id",
            "artifact_id",
        ):
            value = record.get(key)
            if isinstance(value, str):
                return value
        raise ContractError("record has no recognised identifier")

    def put(self, principal: Principal, record: Mapping[str, Any]) -> str:
        verify_record(record)
        brand_id = record.get("brand_id")
        if brand_id != principal.brand_id:
            self._audit(principal, "write", "DENY_TENANT")
            raise AuthorizationError("cross-tenant write denied")
        record_type = record.get("artifact_type")
        allowed = ROLE_WRITES.get(principal.role_id, frozenset())
        if record_type not in allowed:
            self._audit(principal, "write", "DENY_ROLE")
            raise AuthorizationError(
                f"role {principal.role_id!r} cannot write {record_type!r}"
            )
        record_id = self._record_id(record)
        tenant = self._records.setdefault(principal.brand_id, {})
        tenant[record_id] = copy.deepcopy(dict(record))
        self._audit(principal, "write", "ALLOW")
        return record_id

    def get(self, principal: Principal, record_id: str) -> dict[str, Any]:
        record = self._records.get(principal.brand_id, {}).get(record_id)
        if record is None:
            self._audit(principal, "read", "NOT_FOUND_OR_WRONG_TENANT")
            raise KeyError(record_id)
        self._audit(principal, "read", "ALLOW")
        return copy.deepcopy(record)

    def snapshot(self, principal: Principal) -> bytes:
        records = self._records.get(principal.brand_id, {})
        self._audit(principal, "snapshot", "ALLOW")
        return canonical_bytes(
            {"schema_version": "1.0", "brand_id": principal.brand_id, "records": records}
        )

    @classmethod
    def restore(cls, principal: Principal, snapshot: bytes) -> "TenantStore":
        try:
            payload = json.loads(snapshot)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError("invalid store snapshot") from exc
        if payload.get("brand_id") != principal.brand_id:
            raise AuthorizationError("cross-tenant restore denied")
        records = payload.get("records")
        if not isinstance(records, dict):
            raise ContractError("snapshot records must be an object")
        store = cls()
        tenant: dict[str, dict[str, Any]] = {}
        for record_id, record in records.items():
            verify_record(record)
            if record.get("brand_id") != principal.brand_id:
                raise AuthorizationError("snapshot contains a foreign tenant record")
            if cls._record_id(record) != record_id:
                raise ContractError("snapshot record key does not match record id")
            tenant[record_id] = record
        store._records[principal.brand_id] = tenant
        store._audit(principal, "restore", "ALLOW")
        return store

    def active_learning(
        self, principal: Principal, *, at: datetime | None = None
    ) -> list[dict[str, Any]]:
        now = at or datetime.now(timezone.utc)
        active: list[dict[str, Any]] = []
        for record in self._records.get(principal.brand_id, {}).values():
            if record.get("artifact_type") != "learning_record":
                continue
            try:
                verify_record(record)
            except ContractError:
                continue
            if record.get("validation_status") != "validated":
                continue
            if record.get("lifecycle_status") != "active":
                continue
            if not record.get("evidence_refs"):
                continue
            if parse_time(record["fresh_until"]) <= now:
                continue
            active.append(copy.deepcopy(record))
        return active

    def _audit(self, principal: Principal, action: str, outcome: str) -> None:
        self.audit.append(
            {
                "actor_id": principal.actor_id,
                "role_id": principal.role_id,
                "brand_id": principal.brand_id,
                "action": action,
                "outcome": outcome,
            }
        )
