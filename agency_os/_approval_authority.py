"""Authority-owned attestations for fictional approvals and tenant exports.

The signing key must be supplied and held by a protected authority host and is
never persisted in the worker-writable Paperclip SQLite boundary. Approval,
retention-policy, opaque evidence-reference and recovery signatures use separate
domains. This local reference uses a standard-library HMAC; a production
Paperclip integration must replace it with independently operated signing
identities and key custody.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
from typing import Any, Mapping

from .contracts import ContractError, canonical_bytes, finalize_record, verify_record


_APPROVAL_AUTHORITY_TOKEN = object()


class _FictionalApprovalAuthority:
    """Attest and verify canonical approvals outside their SQLite record store."""

    _ALGORITHM = "HMAC-SHA256"
    _DOMAIN = b"agency-os.paperclip-task-approval.v1\x00"

    def __init__(
        self,
        *,
        authority_id: str,
        signing_key: bytes,
        _construction_token: object,
    ) -> None:
        if _construction_token is not _APPROVAL_AUTHORITY_TOKEN:
            raise ContractError("approval authority construction is denied")
        if not authority_id:
            raise ValueError("approval authority_id is required")
        if not isinstance(signing_key, bytes) or len(signing_key) < 32:
            raise ValueError("approval authority signing key must be at least 32 bytes")
        self._authority_id = authority_id
        self._signing_key = bytes(signing_key)

    def attest(self, approval_body: Mapping[str, Any]) -> dict[str, Any]:
        """Return a finalized approval bearing this authority's attestation."""

        body = copy.deepcopy(dict(approval_body))
        if "content_checksum" in body or "approval_attestation" in body:
            raise ContractError("approval body cannot contain derived authority fields")
        body["approval_attestation"] = {
            "authority_id": self._authority_id,
            "algorithm": self._ALGORITHM,
            "signature": self._signature(body),
        }
        return finalize_record(body)

    def verify(self, approval: Mapping[str, Any]) -> None:
        """Reject records not cryptographically issued by this authority."""

        verify_record(approval)
        body = copy.deepcopy(dict(approval))
        body.pop("content_checksum")
        attestation = body.pop("approval_attestation", None)
        if (
            not isinstance(attestation, Mapping)
            or set(attestation) != {"authority_id", "algorithm", "signature"}
            or attestation.get("authority_id") != self._authority_id
            or attestation.get("algorithm") != self._ALGORITHM
            or not isinstance(attestation.get("signature"), str)
        ):
            raise ContractError("Paperclip approval authority attestation is invalid")
        expected = self._signature(body)
        if not hmac.compare_digest(attestation["signature"], expected):
            raise ContractError("Paperclip approval authority attestation is invalid")

    def _signature(self, body: Mapping[str, Any]) -> str:
        return hmac.new(
            self._signing_key,
            self._DOMAIN + canonical_bytes(body),
            hashlib.sha256,
        ).hexdigest()


class _FictionalAuditRetentionAuthority:
    """Authenticate retention policies and opaque evidence references."""

    _ALGORITHM = "HMAC-SHA256"
    _POLICY_DOMAIN = b"agency-os.audit-retention-policy.v1\x00"
    _EVIDENCE_REFERENCE_DOMAIN = b"agency-os.audit-expiration-evidence.v1\x00"

    def __init__(
        self,
        *,
        authority_id: str,
        signing_key: bytes,
        _construction_token: object,
    ) -> None:
        if _construction_token is not _APPROVAL_AUTHORITY_TOKEN:
            raise ContractError("audit retention authority construction is denied")
        if not authority_id:
            raise ValueError("audit retention authority_id is required")
        if not isinstance(signing_key, bytes) or len(signing_key) < 32:
            raise ValueError(
                "audit retention authority signing key must be at least 32 bytes"
            )
        self._authority_id = authority_id
        self._signing_key = bytes(signing_key)

    def attest(self, policy_body: Mapping[str, Any]) -> dict[str, Any]:
        """Return a finalized policy bearing this authority's attestation."""

        body = copy.deepcopy(dict(policy_body))
        if "content_checksum" in body or "audit_retention_attestation" in body:
            raise ContractError(
                "audit retention policy cannot contain derived authority fields"
            )
        body["audit_retention_attestation"] = {
            "authority_id": self._authority_id,
            "algorithm": self._ALGORITHM,
            "signature": self._signature(self._POLICY_DOMAIN, body),
        }
        return finalize_record(body)

    def verify(self, policy: Mapping[str, Any]) -> None:
        """Reject retention policies not issued by this protected authority."""

        verify_record(policy)
        body = copy.deepcopy(dict(policy))
        body.pop("content_checksum")
        attestation = body.pop("audit_retention_attestation", None)
        if (
            not isinstance(attestation, Mapping)
            or set(attestation) != {"authority_id", "algorithm", "signature"}
            or attestation.get("authority_id") != self._authority_id
            or attestation.get("algorithm") != self._ALGORITHM
            or not isinstance(attestation.get("signature"), str)
        ):
            raise ContractError("audit retention authority attestation is invalid")
        expected = self._signature(self._POLICY_DOMAIN, body)
        if not hmac.compare_digest(attestation["signature"], expected):
            raise ContractError("audit retention authority attestation is invalid")

    def evidence_reference(self, brand_id: str, evidence_ref: str) -> str:
        """Return a tenant-bound opaque binding without retaining caller text."""

        signature = self._signature(
            self._EVIDENCE_REFERENCE_DOMAIN,
            {"brand_id": brand_id, "evidence_ref": evidence_ref},
        )
        return f"hmac-sha256:{signature}"

    def _signature(self, domain: bytes, body: Mapping[str, Any]) -> str:
        return hmac.new(
            self._signing_key,
            domain + canonical_bytes(body),
            hashlib.sha256,
        ).hexdigest()


class _FictionalRecoveryAuthority:
    """Attest and verify tenant exports inside the protected authority host."""

    _ALGORITHM = "HMAC-SHA256"
    _DOMAINS = {
        "artifact": b"agency-os.tenant-artifact-export.v1\x00",
        "authority": b"agency-os.tenant-authority-export.v1\x00",
    }

    def __init__(
        self,
        *,
        authority_id: str,
        signing_key: bytes,
        scope: str = "artifact",
        _construction_token: object,
    ) -> None:
        if _construction_token is not _APPROVAL_AUTHORITY_TOKEN:
            raise ContractError("recovery authority construction is denied")
        if not authority_id:
            raise ValueError("recovery authority_id is required")
        if not isinstance(signing_key, bytes) or len(signing_key) < 32:
            raise ValueError("recovery authority signing key must be at least 32 bytes")
        if scope not in self._DOMAINS:
            raise ValueError("recovery authority scope is invalid")
        self._authority_id = authority_id
        self._signing_key = bytes(signing_key)
        self._domain = self._DOMAINS[scope]

    def attest(self, tenant_export: Mapping[str, Any]) -> dict[str, str]:
        """Return an origin attestation bound to an exact tenant export."""

        body = self._attestation_body(tenant_export)
        return {**body, "signature": self._signature(body)}

    def verify(
        self,
        tenant_export: Mapping[str, Any],
        attestation: object,
    ) -> None:
        """Reject exports not cryptographically issued by this authority."""

        expected_body = self._attestation_body(tenant_export)
        if (
            not isinstance(attestation, Mapping)
            or set(attestation) != {*expected_body, "signature"}
            or any(attestation.get(key) != value for key, value in expected_body.items())
            or not isinstance(attestation.get("signature"), str)
        ):
            raise ContractError("tenant export authority attestation is invalid")
        expected_signature = self._signature(expected_body)
        if not hmac.compare_digest(attestation["signature"], expected_signature):
            raise ContractError("tenant export authority attestation is invalid")

    def _attestation_body(self, tenant_export: Mapping[str, Any]) -> dict[str, str]:
        brand_id = tenant_export.get("brand_id")
        exported_at = tenant_export.get("exported_at")
        export_checksum = tenant_export.get("export_checksum")
        if not all(
            isinstance(value, str) and value
            for value in (brand_id, exported_at, export_checksum)
        ):
            raise ContractError("tenant export attestation fields are invalid")
        return {
            "authority_id": self._authority_id,
            "algorithm": self._ALGORITHM,
            "brand_id": brand_id,
            "exported_at": exported_at,
            "export_checksum": export_checksum,
        }

    def _signature(self, body: Mapping[str, Any]) -> str:
        return hmac.new(
            self._signing_key,
            self._domain + canonical_bytes(body),
            hashlib.sha256,
        ).hexdigest()
