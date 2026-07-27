"""Authority-owned attestation for fictional Paperclip approvals.

The signing key must be supplied and held by a protected authority host and is
never persisted in the worker-writable Paperclip SQLite boundary. This local
reference uses a standard-library HMAC; a production Paperclip integration must
replace it with its independently operated approval identity and key custody.
"""

from __future__ import annotations

import copy
import hashlib
import hmac
from typing import Any, Mapping

from .contracts import ContractError, canonical_bytes, finalize_record, verify_record


class FictionalApprovalAuthority:
    """Attest and verify canonical approvals outside their SQLite record store."""

    _ALGORITHM = "HMAC-SHA256"
    _DOMAIN = b"agency-os.paperclip-task-approval.v1\x00"

    def __init__(self, *, authority_id: str, signing_key: bytes) -> None:
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
