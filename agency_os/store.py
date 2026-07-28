"""Tenant-scoped in-memory store and role authorization."""

from __future__ import annotations

import copy
import json
import threading
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
    "paperclip-board-observer": frozenset({"paperclip_approval_evidence"}),
}

ROLE_READS: dict[str, frozenset[str]] = {
    "agency-director": frozenset({"*"}),
    "platform-assurance-reviewer": frozenset({"*"}),
    "brand-brief-steward": frozenset({"brand_profile", "campaign_brief"}),
    "search-content-strategist": frozenset(
        {"brand_profile", "campaign_brief", "research_pack", "content_brief"}
    ),
    "content-producer": frozenset(
        {"brand_profile", "content_brief", "draft_asset_package"}
    ),
    "search-answer-optimiser": frozenset(
        {"draft_asset_package", "complete_asset_package"}
    ),
    "editorial-integrity-qa": frozenset(
        {
            "complete_asset_package",
            "qa_verdict",
            "qa_passed_asset_package",
            "failure_observation",
        }
    ),
    "publishing-operator": frozenset(
        {"publication_manifest", "publication_receipt", "failure_observation"}
    ),
    "growth-intelligence-analyst": frozenset(
        {
            "publication_receipt",
            "performance_snapshot",
            "candidate_learning",
            "learning_context_manifest",
            "learning_record",
            "failure_observation",
        }
    ),
    "human-approver": frozenset({"publication_manifest", "approval_record"}),
    "paperclip-board-observer": frozenset({"paperclip_approval_evidence"}),
}

RECORD_ID_FIELDS: dict[str, str] = {
    "publication_manifest": "manifest_id",
    "approval_record": "approval_id",
    "paperclip_approval_evidence": "paperclip_approval_id",
    "publication_receipt": "receipt_id",
    "learning_record": "learning_record_id",
    "learning_context_manifest": "context_manifest_id",
    "failure_observation": "failure_observation_id",
    "candidate_learning": "candidate_learning_id",
}


class TenantStore:
    def __init__(self) -> None:
        self._records: dict[str, dict[str, dict[str, Any]]] = {}
        self._provenance: dict[str, dict[str, dict[str, str]]] = {}
        self._lock = threading.RLock()
        self.audit: list[dict[str, str]] = []

    @staticmethod
    def _record_id(record: Mapping[str, Any]) -> str:
        record_type = record.get("artifact_type")
        key = RECORD_ID_FIELDS.get(str(record_type), "artifact_id")
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
        raise ContractError("record has no recognised identifier")

    @staticmethod
    def _can_read(principal: Principal, record_type: object) -> bool:
        allowed = ROLE_READS.get(principal.role_id, frozenset())
        return "*" in allowed or record_type in allowed

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
        if (
            record_type == "approval_record"
            and record.get("approver_id") != principal.actor_id
        ):
            self._audit(principal, "write", "DENY_APPROVER_IDENTITY")
            raise AuthorizationError("approval signer does not match authenticated actor")
        if (
            record_type == "paperclip_approval_evidence"
            and record.get("observed_by") != principal.actor_id
        ):
            self._audit(principal, "write", "DENY_OBSERVER_IDENTITY")
            raise AuthorizationError(
                "Paperclip observer does not match authenticated actor"
            )
        record_id = self._record_id(record)
        new_record = copy.deepcopy(dict(record))
        with self._lock:
            tenant = self._records.setdefault(principal.brand_id, {})
            existing = tenant.get(record_id)
            if existing is not None:
                if existing != new_record:
                    self._audit(principal, "write", "DENY_IMMUTABLE_CONFLICT")
                    raise ContractError(f"record {record_id!r} is immutable")
                self._audit(principal, "write", "ALLOW_IDEMPOTENT")
                return record_id
            tenant[record_id] = new_record
            self._provenance.setdefault(principal.brand_id, {})[record_id] = {
                "actor_id": principal.actor_id,
                "role_id": principal.role_id,
            }
        self._audit(principal, "write", "ALLOW")
        return record_id

    def get(self, principal: Principal, record_id: str) -> dict[str, Any]:
        with self._lock:
            record = copy.deepcopy(
                self._records.get(principal.brand_id, {}).get(record_id)
            )
        if record is None:
            self._audit(principal, "read", "NOT_FOUND_OR_WRONG_TENANT")
            raise KeyError(record_id)
        if not self._can_read(principal, record.get("artifact_type")):
            self._audit(principal, "read", "DENY_ROLE")
            raise AuthorizationError(
                f"role {principal.role_id!r} cannot read "
                f"{record.get('artifact_type')!r}"
            )
        self._audit(principal, "read", "ALLOW")
        return record

    def resolve_approval(
        self, brand_id: str, approval_id: str
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """Resolve an immutable approval plus its authenticated write provenance."""
        with self._lock:
            record = copy.deepcopy(self._records.get(brand_id, {}).get(approval_id))
            provenance = copy.deepcopy(
                self._provenance.get(brand_id, {}).get(approval_id)
            )
        if (
            record is None
            or provenance is None
            or record.get("artifact_type") != "approval_record"
        ):
            raise KeyError(approval_id)
        verify_record(record)
        if self._record_id(record) != approval_id:
            raise ContractError("stored approval key does not match approval id")
        return record, provenance

    def resolve_paperclip_approval_evidence(
        self, brand_id: str, paperclip_approval_id: str
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """Resolve immutable Paperclip evidence plus observer provenance."""
        with self._lock:
            record = copy.deepcopy(
                self._records.get(brand_id, {}).get(paperclip_approval_id)
            )
            provenance = copy.deepcopy(
                self._provenance.get(brand_id, {}).get(paperclip_approval_id)
            )
        if (
            record is None
            or provenance is None
            or record.get("artifact_type") != "paperclip_approval_evidence"
        ):
            raise KeyError(paperclip_approval_id)
        verify_record(record)
        if self._record_id(record) != paperclip_approval_id:
            raise ContractError(
                "stored Paperclip evidence key does not match approval id"
            )
        return record, provenance

    def snapshot(self, principal: Principal) -> bytes:
        if principal.role_id != "agency-director":
            self._audit(principal, "snapshot", "DENY_ROLE")
            raise AuthorizationError("only the agency director may snapshot a tenant")
        with self._lock:
            records = copy.deepcopy(self._records.get(principal.brand_id, {}))
            provenance = copy.deepcopy(
                self._provenance.get(principal.brand_id, {})
            )
        self._audit(principal, "snapshot", "ALLOW")
        return canonical_bytes(
            {
                "schema_version": "1.0",
                "brand_id": principal.brand_id,
                "records": records,
                "provenance": provenance,
            }
        )

    @classmethod
    def restore(cls, principal: Principal, snapshot: bytes) -> "TenantStore":
        if principal.role_id != "agency-director":
            raise AuthorizationError("only the agency director may restore a tenant")
        try:
            payload = json.loads(snapshot)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError("invalid store snapshot") from exc
        if payload.get("brand_id") != principal.brand_id:
            raise AuthorizationError("cross-tenant restore denied")
        records = payload.get("records")
        if not isinstance(records, dict):
            raise ContractError("snapshot records must be an object")
        provenance = payload.get("provenance")
        if not isinstance(provenance, dict):
            raise ContractError("snapshot provenance must be an object")
        store = cls()
        tenant: dict[str, dict[str, Any]] = {}
        tenant_provenance: dict[str, dict[str, str]] = {}
        for record_id, record in records.items():
            verify_record(record)
            if record.get("brand_id") != principal.brand_id:
                raise AuthorizationError("snapshot contains a foreign tenant record")
            if cls._record_id(record) != record_id:
                raise ContractError("snapshot record key does not match record id")
            record_provenance = provenance.get(record_id)
            if (
                not isinstance(record_provenance, dict)
                or not isinstance(record_provenance.get("actor_id"), str)
                or not isinstance(record_provenance.get("role_id"), str)
            ):
                raise ContractError("snapshot record has invalid provenance")
            tenant[record_id] = record
            tenant_provenance[record_id] = {
                "actor_id": record_provenance["actor_id"],
                "role_id": record_provenance["role_id"],
            }
        store._records[principal.brand_id] = tenant
        store._provenance[principal.brand_id] = tenant_provenance
        store._audit(principal, "restore", "ALLOW")
        return store

    def active_learning(
        self, principal: Principal, *, at: datetime | None = None
    ) -> list[dict[str, Any]]:
        if not self._can_read(principal, "learning_record"):
            self._audit(principal, "active_learning", "DENY_ROLE")
            raise AuthorizationError(
                f"role {principal.role_id!r} cannot read learning records"
            )
        now = at or datetime.now(timezone.utc)
        active: list[dict[str, Any]] = []
        with self._lock:
            records = copy.deepcopy(
                list(self._records.get(principal.brand_id, {}).values())
            )
        for record in records:
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
