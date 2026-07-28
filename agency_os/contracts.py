"""Canonical record construction and validation.

The implementation intentionally uses only the Python standard library so the
fictional release gate can run without downloading dependencies.
"""

from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping


class ContractError(ValueError):
    """A record violates a canonical contract."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise ContractError(f"invalid ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ContractError("timestamps must include a timezone")
    return parsed


def canonical_bytes(value: Any) -> bytes:
    try:
        text = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as exc:
        raise ContractError(f"value is not canonical JSON: {exc}") from exc
    return text.encode("utf-8")


def canonical_checksum(value: Any) -> str:
    return f"sha256:{hashlib.sha256(canonical_bytes(value)).hexdigest()}"


def _without_checksum(record: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(record))
    result.pop("content_checksum", None)
    return result


def finalize_record(record: Mapping[str, Any]) -> dict[str, Any]:
    result = _without_checksum(record)
    result["content_checksum"] = canonical_checksum(result)
    return result


def verify_record(record: Mapping[str, Any]) -> None:
    checksum = record.get("content_checksum")
    if not isinstance(checksum, str):
        raise ContractError("record has no content_checksum")
    expected = canonical_checksum(_without_checksum(record))
    if checksum != expected:
        raise ContractError(f"checksum mismatch: expected {expected}, got {checksum}")


def require_fields(record: Mapping[str, Any], fields: Iterable[str]) -> None:
    missing = [field for field in fields if field not in record]
    if missing:
        raise ContractError(f"missing required fields: {', '.join(missing)}")


def make_envelope(
    *,
    artifact_type: str,
    artifact_id: str,
    brand_id: str,
    campaign_id: str,
    asset_id: str,
    issue_id: str,
    created_by: Mapping[str, str],
    payload: Mapping[str, Any],
    source_artifact_ids: Iterable[str] = (),
    status: str = "draft",
) -> dict[str, Any]:
    if not brand_id.startswith("brand_"):
        raise ContractError("brand_id must use the brand_ prefix")
    record = {
        "schema_version": "1.0",
        "artifact_type": artifact_type,
        "artifact_id": artifact_id,
        "brand_id": brand_id,
        "campaign_id": campaign_id,
        "asset_id": asset_id,
        "paperclip_issue_id": issue_id,
        "created_at": utc_now(),
        "created_by": dict(created_by),
        "source_artifact_ids": list(source_artifact_ids),
        "status": status,
        "payload": copy.deepcopy(dict(payload)),
    }
    return finalize_record(record)


def validate_asset_package(record: Mapping[str, Any], expected_type: str) -> None:
    verify_record(record)
    require_fields(
        record,
        (
            "schema_version",
            "artifact_type",
            "artifact_id",
            "brand_id",
            "campaign_id",
            "asset_id",
            "paperclip_issue_id",
            "created_at",
            "created_by",
            "payload",
        ),
    )
    if record["artifact_type"] != expected_type:
        raise ContractError(
            f"expected artifact_type {expected_type!r}, got {record['artifact_type']!r}"
        )
    payload = record["payload"]
    require_fields(payload, ("public_fields", "internal_notes"))
    if not isinstance(payload["public_fields"], Mapping):
        raise ContractError("public_fields must be an object")
    if not isinstance(payload["internal_notes"], list):
        raise ContractError("internal_notes must be an array")


def make_publication_manifest(
    *,
    manifest_id: str,
    qa_package: Mapping[str, Any],
    destination_ref: str,
    environment: str,
    operation: str,
    schedule_window: Mapping[str, str],
    transformation_version: str,
    child_checksums: Iterable[str] = (),
) -> dict[str, Any]:
    validate_asset_package(qa_package, "qa_passed_asset_package")
    public_fields = copy.deepcopy(qa_package["payload"]["public_fields"])
    manifest = {
        "schema_version": "1.0",
        "artifact_type": "publication_manifest",
        "manifest_id": manifest_id,
        "brand_id": qa_package["brand_id"],
        "campaign_id": qa_package["campaign_id"],
        "asset_id": qa_package["asset_id"],
        "qa_package_id": qa_package["artifact_id"],
        "qa_package_checksum": qa_package["content_checksum"],
        "child_checksums": sorted(child_checksums),
        "destination_ref": destination_ref,
        "environment": environment,
        "operation": operation,
        "schedule_window": dict(schedule_window),
        "transformation_version": transformation_version,
        "public_fields": public_fields,
        "created_at": utc_now(),
    }
    require_fields(manifest["schedule_window"], ("starts_at", "ends_at"))
    starts_at = parse_time(manifest["schedule_window"]["starts_at"])
    ends_at = parse_time(manifest["schedule_window"]["ends_at"])
    if starts_at >= ends_at:
        raise ContractError("schedule_window starts_at must precede ends_at")
    return finalize_record(manifest)


def make_approval_record(
    *,
    approval_id: str,
    manifest: Mapping[str, Any],
    approver_id: str,
    authority_role: str,
    decided_at: str,
    expires_at: str,
    paperclip_approval_id: str,
    paperclip_approval_evidence_checksum: str,
) -> dict[str, Any]:
    verify_record(manifest)
    record = {
        "schema_version": "1.0",
        "artifact_type": "approval_record",
        "approval_id": approval_id,
        "decision": "APPROVED",
        "brand_id": manifest["brand_id"],
        "manifest_id": manifest["manifest_id"],
        "manifest_checksum": manifest["content_checksum"],
        "artifact_id": manifest["qa_package_id"],
        "artifact_checksum": manifest["qa_package_checksum"],
        "destination_ref": manifest["destination_ref"],
        "environment": manifest["environment"],
        "operation": manifest["operation"],
        "schedule_window": copy.deepcopy(manifest["schedule_window"]),
        "approver_id": approver_id,
        "authority_role": authority_role,
        "conditions": [],
        "decided_at": decided_at,
        "expires_at": expires_at,
    }
    decision_time = parse_time(decided_at)
    paperclip_evidence = (
        paperclip_approval_id,
        paperclip_approval_evidence_checksum,
    )
    if not all(isinstance(value, str) and value for value in paperclip_evidence):
        raise ContractError("Paperclip approval evidence is required")
    record["paperclip_approval_id"] = paperclip_approval_id
    record["paperclip_approval_evidence_checksum"] = (
        paperclip_approval_evidence_checksum
    )
    expiry_time = parse_time(expires_at)
    if decision_time >= expiry_time:
        raise ContractError("approval decided_at must precede expires_at")
    return finalize_record(record)


def make_capability_record(
    *,
    capability_id: str,
    brand_id: str,
    actor_id: str,
    role_id: str,
    destination_ref: str,
    environment: str,
    operation: str,
    action_class: str,
    data_class: str,
    issued_by: str,
    issued_at: str,
    not_before: str,
    expires_at: str,
) -> dict[str, Any]:
    record = {
        "schema_version": "1.0",
        "artifact_type": "capability_record",
        "capability_id": capability_id,
        "brand_id": brand_id,
        "actor_id": actor_id,
        "role_id": role_id,
        "destination_ref": destination_ref,
        "environment": environment,
        "operation": operation,
        "action_class": action_class,
        "data_class": data_class,
        "issued_by": issued_by,
        "issued_at": issued_at,
        "not_before": not_before,
        "expires_at": expires_at,
        "status": "active",
    }
    issue_time = parse_time(issued_at)
    start_time = parse_time(not_before)
    expiry_time = parse_time(expires_at)
    if issue_time > start_time:
        raise ContractError("capability cannot become valid before it is issued")
    if start_time >= expiry_time:
        raise ContractError("capability not_before must precede expires_at")
    return finalize_record(record)


def validate_learning_record(record: Mapping[str, Any]) -> None:
    verify_record(record)
    require_fields(
        record,
        (
            "artifact_type",
            "learning_record_id",
            "brand_id",
            "validation_status",
            "lifecycle_status",
            "reuse_scope",
            "evidence_refs",
            "fresh_until",
            "supersedes",
        ),
    )
    if record["artifact_type"] != "learning_record":
        raise ContractError("not a LearningRecord")
    if record["validation_status"] != "validated":
        raise ContractError("learning record is not validated")
    if record["lifecycle_status"] != "active":
        raise ContractError("learning record is not active")
    if not record["evidence_refs"]:
        raise ContractError("learning record has no evidence")
