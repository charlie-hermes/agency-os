"""Fail-closed publication gateway with a deterministic mock destination."""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Callable, Mapping

from .capabilities import (
    CapabilityDispatchError,
    CapabilityDriftError,
    CapabilityError,
    CapabilityExpiredError,
    CapabilityInactiveError,
    CapabilityNotYetEffectiveError,
    CapabilityRegistry,
)
from .contracts import (
    ContractError,
    canonical_checksum,
    finalize_record,
    parse_time,
    utc_now,
    verify_record,
)
from .ledger import ActionLedger, LedgerError
from .store import Principal, TenantStore


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
        self,
        *,
        capability_id: str,
        capability_registry: CapabilityRegistry,
        publisher: MockPublisher,
        approval_store: TenantStore,
        approval_authorities: Mapping[str, Mapping[str, list[str] | tuple[str, ...]]],
        action_ledger: ActionLedger,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.capability_id = capability_id
        self.capability_registry = capability_registry
        self.publisher = publisher
        self.approval_store = approval_store
        self.approval_authorities = copy.deepcopy(dict(approval_authorities))
        self.action_ledger = action_ledger
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.audit: list[dict[str, Any]] = []

    def publish(
        self,
        *,
        principal: Principal,
        manifest: Mapping[str, Any],
        approval_id: str,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        current_time = now or self._clock()
        capability, capability_status = self._resolve_capability(principal)
        try:
            approval, approval_provenance = self.approval_store.resolve_approval(
                principal.brand_id, approval_id
            )
        except (KeyError, ContractError) as exc:
            self._deny(
                "APPROVAL_NOT_AUTHORITATIVE",
                {"brand_id": principal.brand_id},
                cause=exc,
            )
        self._preflight(
            principal,
            manifest,
            approval,
            approval_provenance,
            capability,
            capability_status,
            current_time,
        )
        request_binding = {
            "actor_id": principal.actor_id,
            "role_id": principal.role_id,
            "brand_id": principal.brand_id,
            "capability_id": capability["capability_id"],
            "capability_checksum": capability["content_checksum"],
            "manifest_checksum": manifest["content_checksum"],
            "approval_checksum": approval["content_checksum"],
            "destination_ref": manifest["destination_ref"],
            "environment": manifest["environment"],
            "operation": manifest["operation"],
            "schedule_window": manifest["schedule_window"],
            "idempotency_key": idempotency_key,
        }
        request_checksum = canonical_checksum(request_binding)
        try:
            reservation = self.action_ledger.reserve(
                principal.brand_id, idempotency_key, request_checksum
            )
        except LedgerError as exc:
            self._deny("LEDGER_UNAVAILABLE", request_binding, cause=exc)
        if reservation.status == "CONFLICT":
            self._deny("IDEMPOTENCY_KEY_REBOUND", request_binding)
        if reservation.status == "BLOCKED":
            self._deny("RECONCILIATION_REQUIRED", request_binding)
        if reservation.status == "REPLAY":
            receipt = reservation.receipt
            if receipt is None:
                self._deny("LEDGER_RECEIPT_INVALID", request_binding)
            try:
                verify_record(receipt)
            except ContractError as exc:
                self._deny("LEDGER_RECEIPT_INVALID", request_binding, cause=exc)
            replay_pairs = (
                ("brand_id", principal.brand_id),
                ("idempotency_key", idempotency_key),
                ("request_binding_checksum", request_checksum),
                ("state", "PUBLISHED"),
            )
            if any(receipt.get(field) != expected for field, expected in replay_pairs):
                self._deny("LEDGER_RECEIPT_INVALID", request_binding)
            self.audit.append(
                {
                    "outcome": "ALLOW_IDEMPOTENT_REPLAY",
                    "brand_id": principal.brand_id,
                    "request_binding_checksum": request_checksum,
                }
            )
            return copy.deepcopy(receipt)
        if reservation.status != "RESERVED":
            self._deny("LEDGER_UNAVAILABLE", request_binding)
        try:
            external = self.capability_registry.authorized_dispatch(
                principal.brand_id,
                self.capability_id,
                capability["content_checksum"],
                clock=self._clock,
                pre_dispatch=lambda dispatch_time: self._validate_time_windows(
                    manifest, approval, dispatch_time
                ),
                dispatch=lambda: self.publisher.publish(
                    public_fields=manifest["public_fields"],
                    idempotency_key=idempotency_key,
                ),
            )
        except CapabilityNotYetEffectiveError as exc:
            self._deny("CAPABILITY_NOT_YET_EFFECTIVE", request_binding, cause=exc)
        except CapabilityExpiredError as exc:
            self._deny("CAPABILITY_EXPIRED", request_binding, cause=exc)
        except CapabilityInactiveError as exc:
            self._deny("CAPABILITY_INACTIVE", request_binding, cause=exc)
        except CapabilityDriftError as exc:
            self._deny("CAPABILITY_DRIFT", request_binding, cause=exc)
        except (KeyError, ContractError, CapabilityError) as exc:
            self._deny("CAPABILITY_NOT_AUTHORITATIVE", request_binding, cause=exc)
        except CapabilityDispatchError as exc:
            try:
                self.action_ledger.mark_unknown(
                    principal.brand_id, idempotency_key, request_checksum
                )
            except LedgerError:
                pass
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
        try:
            self.action_ledger.complete(
                principal.brand_id, idempotency_key, request_checksum, receipt
            )
        except LedgerError as exc:
            try:
                self.action_ledger.mark_unknown(
                    principal.brand_id, idempotency_key, request_checksum
                )
            except LedgerError:
                pass
            self._deny("EXTERNAL_RESULT_UNKNOWN", request_binding, cause=exc)
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
        approval_provenance: Mapping[str, str],
        capability: Mapping[str, Any],
        capability_status: str,
        now: datetime,
    ) -> None:
        try:
            verify_record(manifest)
            verify_record(approval)
            verify_record(capability)
        except ContractError as exc:
            self._deny("INVALID_CHECKSUM", {"brand_id": principal.brand_id}, cause=exc)
        if principal.role_id != "publishing-operator":
            self._deny("ROLE_DENIED", {"brand_id": principal.brand_id})
        self._validate_capability(
            principal, manifest, capability, capability_status, now
        )
        if (
            approval_provenance.get("actor_id") != approval.get("approver_id")
            or approval_provenance.get("role_id") != "human-approver"
        ):
            self._deny("APPROVAL_PROVENANCE_INVALID", dict(manifest))
        brand_policy = self.approval_authorities.get(principal.brand_id, {})
        allowed_approvers = brand_policy.get(str(approval.get("authority_role")), ())
        if approval.get("approver_id") not in allowed_approvers:
            self._deny("APPROVAL_AUTHORITY_DENIED", dict(manifest))
        if approval.get("decision") != "APPROVED":
            self._deny("NOT_APPROVED", dict(manifest))
        conditions = approval.get("conditions")
        if not isinstance(conditions, list):
            self._deny("APPROVAL_CONDITIONS_INVALID", dict(manifest))
        if conditions:
            self._deny("APPROVAL_CONDITIONS_UNEVALUATED", dict(manifest))
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
        self._validate_time_windows(manifest, approval, now)

    def _validate_time_windows(
        self,
        manifest: Mapping[str, Any],
        approval: Mapping[str, Any],
        now: datetime,
    ) -> None:
        if parse_time(approval["decided_at"]) > now:
            self._deny("APPROVAL_NOT_YET_EFFECTIVE", dict(manifest))
        if parse_time(approval["expires_at"]) <= now:
            self._deny("APPROVAL_EXPIRED", dict(manifest))
        if parse_time(manifest["schedule_window"]["starts_at"]) > now:
            self._deny("SCHEDULE_WINDOW_NOT_STARTED", dict(manifest))
        if parse_time(manifest["schedule_window"]["ends_at"]) <= now:
            self._deny("SCHEDULE_WINDOW_EXPIRED", dict(manifest))

    def _resolve_capability(
        self, principal: Principal
    ) -> tuple[dict[str, Any], str]:
        try:
            return self.capability_registry.resolve(
                principal.brand_id, self.capability_id
            )
        except (KeyError, ContractError, CapabilityError) as exc:
            self._deny(
                "CAPABILITY_NOT_AUTHORITATIVE",
                {"brand_id": principal.brand_id},
                cause=exc,
            )

    def _validate_capability(
        self,
        principal: Principal,
        manifest: Mapping[str, Any],
        capability: Mapping[str, Any],
        registry_status: str,
        now: datetime,
    ) -> None:
        try:
            verify_record(capability)
        except ContractError as exc:
            self._deny(
                "CAPABILITY_NOT_AUTHORITATIVE",
                {"brand_id": principal.brand_id},
                cause=exc,
            )
        if capability.get("status") != "active" or registry_status != "active":
            self._deny("CAPABILITY_INACTIVE", {"brand_id": principal.brand_id})
        exact_pairs = (
            ("brand_id", principal.brand_id, capability.get("brand_id")),
            ("actor_id", principal.actor_id, capability.get("actor_id")),
            ("role_id", principal.role_id, capability.get("role_id")),
            (
                "destination_ref",
                manifest.get("destination_ref"),
                capability.get("destination_ref"),
            ),
            (
                "environment",
                manifest.get("environment"),
                capability.get("environment"),
            ),
            (
                "operation",
                manifest.get("operation"),
                capability.get("operation"),
            ),
        )
        for field, actual, expected in exact_pairs:
            if actual != expected:
                self._deny(f"CAPABILITY_{field.upper()}_DENIED", dict(manifest))
        if capability.get("action_class") != "external_write":
            self._deny("CAPABILITY_ACTION_CLASS_DENIED", dict(manifest))
        if capability.get("data_class") != "public_content":
            self._deny("CAPABILITY_DATA_CLASS_DENIED", dict(manifest))
        try:
            not_before = parse_time(capability["not_before"])
            expires_at = parse_time(capability["expires_at"])
        except (KeyError, ContractError) as exc:
            self._deny("CAPABILITY_NOT_AUTHORITATIVE", dict(manifest), cause=exc)
        if not_before > now:
            self._deny("CAPABILITY_NOT_YET_EFFECTIVE", dict(manifest))
        if expires_at <= now:
            self._deny("CAPABILITY_EXPIRED", dict(manifest))

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
