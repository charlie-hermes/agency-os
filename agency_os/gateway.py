"""Fail-closed publication gateway with a deterministic mock destination."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Mapping

from .contracts import (
    ContractError,
    canonical_checksum,
    finalize_record,
    parse_time,
    utc_now,
    verify_record,
)
from .store import Principal


class GatewayDenied(PermissionError):
    """The action did not satisfy the exact publication bindings."""


class MockPublisher:
    """A local destination that never performs network I/O."""

    def __init__(self) -> None:
        self.calls = 0
        self.objects: dict[str, dict[str, Any]] = {}

    def publish(
        self, *, public_fields: Mapping[str, Any], idempotency_key: str
    ) -> dict[str, Any]:
        self.calls += 1
        external_id = f"mock_{len(self.objects) + 1}"
        result = {
            "external_id": external_id,
            "external_url": f"mock://published/{external_id}",
            "state": "PUBLISHED",
            "rendered_public_fields": copy.deepcopy(dict(public_fields)),
        }
        self.objects[idempotency_key] = result
        return copy.deepcopy(result)


class ActionGateway:
    def __init__(
        self, *, capability: Mapping[str, Any], publisher: MockPublisher
    ) -> None:
        self.capability = copy.deepcopy(dict(capability))
        self.publisher = publisher
        self._ledger: dict[str, dict[str, Any]] = {}
        self.audit: list[dict[str, Any]] = []

    def publish(
        self,
        *,
        principal: Principal,
        manifest: Mapping[str, Any],
        approval: Mapping[str, Any],
        idempotency_key: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current_time = now or datetime.now(timezone.utc)
        self._preflight(principal, manifest, approval, current_time)
        request_binding = {
            "actor_id": principal.actor_id,
            "role_id": principal.role_id,
            "brand_id": principal.brand_id,
            "capability_id": self.capability["capability_id"],
            "manifest_checksum": manifest["content_checksum"],
            "approval_checksum": approval["content_checksum"],
            "destination_ref": manifest["destination_ref"],
            "environment": manifest["environment"],
            "operation": manifest["operation"],
            "schedule_window": manifest["schedule_window"],
            "idempotency_key": idempotency_key,
        }
        request_checksum = canonical_checksum(request_binding)
        existing = self._ledger.get(idempotency_key)
        if existing is not None:
            if existing["request_checksum"] != request_checksum:
                self._deny("IDEMPOTENCY_KEY_REBOUND", request_binding)
            if existing["state"] in {"REQUESTED", "UNKNOWN"}:
                self._deny("RECONCILIATION_REQUIRED", request_binding)
            self.audit.append(
                {
                    "outcome": "ALLOW_IDEMPOTENT_REPLAY",
                    "brand_id": principal.brand_id,
                    "request_binding_checksum": request_checksum,
                }
            )
            return copy.deepcopy(existing["receipt"])

        # Persist intent before the external call.
        self._ledger[idempotency_key] = {
            "request_checksum": request_checksum,
            "state": "REQUESTED",
            "receipt": None,
        }
        try:
            external = self.publisher.publish(
                public_fields=manifest["public_fields"],
                idempotency_key=idempotency_key,
            )
        except Exception as exc:
            self._ledger[idempotency_key]["state"] = "UNKNOWN"
            self._deny("EXTERNAL_RESULT_UNKNOWN", request_binding, cause=exc)

        receipt = finalize_record(
            {
                "schema_version": "1.0",
                "artifact_type": "publication_receipt",
                "receipt_id": f"receipt_{idempotency_key}",
                "brand_id": principal.brand_id,
                "manifest_id": manifest["manifest_id"],
                "manifest_checksum": manifest["content_checksum"],
                "approval_id": approval["approval_id"],
                "approval_checksum": approval["content_checksum"],
                "artifact_id": manifest["qa_package_id"],
                "artifact_checksum": manifest["qa_package_checksum"],
                "destination_ref": manifest["destination_ref"],
                "environment": manifest["environment"],
                "operation": manifest["operation"],
                "adapter_version": manifest["transformation_version"],
                "idempotency_key": idempotency_key,
                "request_binding_checksum": request_checksum,
                "external_id": external["external_id"],
                "external_url": external["external_url"],
                "state": external["state"],
                "published_at": utc_now(),
                "validation": {
                    "state_verified": external["state"] == "PUBLISHED",
                    "public_fields_checksum": canonical_checksum(
                        external["rendered_public_fields"]
                    ),
                    "matches_manifest": external["rendered_public_fields"]
                    == manifest["public_fields"],
                },
                "replayed": False,
            }
        )
        self._ledger[idempotency_key] = {
            "request_checksum": request_checksum,
            "state": receipt["state"],
            "receipt": receipt,
        }
        self.audit.append(
            {
                "outcome": "ALLOW",
                "brand_id": principal.brand_id,
                "request_binding_checksum": request_checksum,
                "manifest_checksum": manifest["content_checksum"],
            }
        )
        return copy.deepcopy(receipt)

    def _preflight(
        self,
        principal: Principal,
        manifest: Mapping[str, Any],
        approval: Mapping[str, Any],
        now: datetime,
    ) -> None:
        try:
            verify_record(manifest)
            verify_record(approval)
        except ContractError as exc:
            self._deny("INVALID_CHECKSUM", {"brand_id": principal.brand_id}, cause=exc)
        if principal.role_id != "publishing-operator":
            self._deny("ROLE_DENIED", {"brand_id": principal.brand_id})
        if self.capability.get("status") != "active":
            self._deny("CAPABILITY_INACTIVE", {"brand_id": principal.brand_id})
        exact_pairs = (
            ("brand_id", principal.brand_id),
            ("brand_id", self.capability.get("brand_id")),
            ("destination_ref", self.capability.get("destination_ref")),
            ("environment", self.capability.get("environment")),
            ("operation", self.capability.get("operation")),
        )
        for field, expected in exact_pairs:
            if manifest.get(field) != expected:
                self._deny(f"MANIFEST_{field.upper()}_MISMATCH", dict(manifest))
        if principal.role_id not in self.capability.get("allowed_role_ids", []):
            self._deny("CAPABILITY_ROLE_DENIED", dict(manifest))
        if approval.get("decision") != "APPROVED":
            self._deny("NOT_APPROVED", dict(manifest))
        bindings = (
            "brand_id",
            "destination_ref",
            "environment",
            "operation",
            "schedule_window",
        )
        for field in bindings:
            if approval.get(field) != manifest.get(field):
                self._deny(f"APPROVAL_{field.upper()}_MISMATCH", dict(manifest))
        if approval.get("manifest_id") != manifest.get("manifest_id"):
            self._deny("APPROVAL_MANIFEST_ID_MISMATCH", dict(manifest))
        if approval.get("manifest_checksum") != manifest.get("content_checksum"):
            self._deny("APPROVAL_MANIFEST_CHECKSUM_MISMATCH", dict(manifest))
        if approval.get("artifact_checksum") != manifest.get("qa_package_checksum"):
            self._deny("APPROVAL_ARTIFACT_CHECKSUM_MISMATCH", dict(manifest))
        if parse_time(approval["decided_at"]) > now:
            self._deny("APPROVAL_NOT_YET_EFFECTIVE", dict(manifest))
        if parse_time(approval["expires_at"]) <= now:
            self._deny("APPROVAL_EXPIRED", dict(manifest))
        if parse_time(manifest["schedule_window"]["starts_at"]) > now:
            self._deny("SCHEDULE_WINDOW_NOT_STARTED", dict(manifest))
        if parse_time(manifest["schedule_window"]["ends_at"]) <= now:
            self._deny("SCHEDULE_WINDOW_EXPIRED", dict(manifest))

    def _deny(
        self,
        reason: str,
        binding: Mapping[str, Any],
        *,
        cause: Exception | None = None,
    ) -> None:
        self.audit.append(
            {
                "outcome": "DENY",
                "reason": reason,
                "brand_id": binding.get("brand_id"),
            }
        )
        error = GatewayDenied(reason)
        if cause is not None:
            raise error from cause
        raise error
