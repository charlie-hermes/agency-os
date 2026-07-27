"""Authoritative, tenant-scoped capability admission for external actions."""

from __future__ import annotations

import copy
import threading
from typing import Any, Mapping

from .contracts import ContractError, parse_time, require_fields, verify_record
from .store import Principal


class CapabilityError(ValueError):
    """A capability record or lifecycle transition is invalid."""


class CapabilityRegistry:
    """Thread-safe authority for immutable grants and immediate suspension."""

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], dict[str, Any]] = {}
        self._suspended: set[tuple[str, str]] = set()
        self._lock = threading.RLock()
        self.audit: list[dict[str, str]] = []

    def register(self, issuer: Principal, record: Mapping[str, Any]) -> str:
        verify_record(record)
        require_fields(
            record,
            (
                "artifact_type",
                "capability_id",
                "brand_id",
                "actor_id",
                "role_id",
                "destination_ref",
                "environment",
                "operation",
                "action_class",
                "data_class",
                "issued_by",
                "issued_at",
                "not_before",
                "expires_at",
                "status",
            ),
        )
        if record["artifact_type"] != "capability_record":
            raise CapabilityError("not a capability record")
        if issuer.role_id != "agency-director":
            self._audit(issuer, str(record["capability_id"]), "DENY_ISSUER_ROLE")
            raise CapabilityError("only the agency director may issue capabilities")
        if record["brand_id"] != issuer.brand_id:
            self._audit(issuer, str(record["capability_id"]), "DENY_TENANT")
            raise CapabilityError("cross-tenant capability issue denied")
        if record["issued_by"] != issuer.actor_id:
            self._audit(issuer, str(record["capability_id"]), "DENY_ISSUER_IDENTITY")
            raise CapabilityError(
                "capability issuer does not match authenticated actor"
            )
        if record["status"] != "active":
            raise CapabilityError("new capability must start active")
        if parse_time(record["issued_at"]) > parse_time(record["not_before"]):
            raise CapabilityError("capability cannot become valid before it is issued")
        if parse_time(record["not_before"]) >= parse_time(record["expires_at"]):
            raise CapabilityError("capability validity window is empty")
        for field in (
            "capability_id",
            "brand_id",
            "actor_id",
            "role_id",
            "destination_ref",
            "environment",
            "operation",
            "action_class",
            "data_class",
        ):
            if not isinstance(record[field], str) or not record[field]:
                raise CapabilityError(f"capability field {field!r} must be non-empty")

        capability_id = record["capability_id"]
        key = (record["brand_id"], capability_id)
        new_record = copy.deepcopy(dict(record))
        with self._lock:
            existing = self._records.get(key)
            if existing is not None:
                if existing != new_record:
                    self._audit(issuer, capability_id, "DENY_IMMUTABLE_CONFLICT")
                    raise ContractError(f"capability {capability_id!r} is immutable")
                self._audit(issuer, capability_id, "ALLOW_IDEMPOTENT")
                return capability_id
            self._records[key] = new_record
        self._audit(issuer, capability_id, "ALLOW")
        return capability_id

    def suspend(self, issuer: Principal, brand_id: str, capability_id: str) -> None:
        if issuer.role_id != "agency-director":
            self._audit(issuer, capability_id, "DENY_SUSPEND_ROLE")
            raise CapabilityError("only the agency director may suspend capabilities")
        if issuer.brand_id != brand_id:
            self._audit(issuer, capability_id, "DENY_SUSPEND_TENANT")
            raise CapabilityError("cross-tenant capability suspension denied")
        key = (brand_id, capability_id)
        with self._lock:
            if key not in self._records:
                self._audit(issuer, capability_id, "DENY_NOT_FOUND")
                raise KeyError(capability_id)
            self._suspended.add(key)
        self._audit(issuer, capability_id, "SUSPEND")

    def resolve(self, brand_id: str, capability_id: str) -> tuple[dict[str, Any], str]:
        key = (brand_id, capability_id)
        with self._lock:
            record = copy.deepcopy(self._records.get(key))
            status = "suspended" if key in self._suspended else "active"
        if record is None:
            raise KeyError(capability_id)
        verify_record(record)
        if (
            record.get("artifact_type") != "capability_record"
            or record.get("capability_id") != capability_id
            or record.get("brand_id") != brand_id
        ):
            raise CapabilityError("stored capability binding is invalid")
        return record, status

    def _audit(self, principal: Principal, capability_id: str, outcome: str) -> None:
        self.audit.append(
            {
                "actor_id": principal.actor_id,
                "role_id": principal.role_id,
                "brand_id": principal.brand_id,
                "capability_id": capability_id,
                "outcome": outcome,
            }
        )
