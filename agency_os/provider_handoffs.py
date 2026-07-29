"""Provider-neutral connected-service and manual-handoff registry."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .contracts import finalize_record


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "config/providers.json"
ALLOWED_MODES = frozenset({"manual_handoff", "typed_adapter"})
ALLOWED_STATUSES = frozenset({"available", "connected", "disabled"})
SECRET_FIELD_PARTS = ("secret", "password", "private_key", "access_token", "api_key")


class ProviderHandoffError(ValueError):
    """Provider configuration or handoff input is unsafe or incomplete."""


def _contains_secret_field(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).lower()
            if any(part in normalized for part in SECRET_FIELD_PARTS):
                return True
            if _contains_secret_field(item):
                return True
    elif isinstance(value, list):
        return any(_contains_secret_field(item) for item in value)
    return False


def load_provider_catalog(path: Path = DEFAULT_CATALOG) -> dict[str, Any]:
    catalog = json.loads(path.read_text())
    entries = catalog.get("providers")
    if not isinstance(entries, list) or not entries:
        raise ProviderHandoffError("provider catalog is empty")
    if _contains_secret_field(catalog):
        raise ProviderHandoffError("provider catalog contains a secret-shaped field")
    observed: set[str] = set()
    for entry in entries:
        required = {
            "capability_id",
            "service_class",
            "mode",
            "status",
            "external_write",
            "operator_action",
        }
        if not isinstance(entry, dict) or not required.issubset(entry):
            raise ProviderHandoffError("provider catalog entry is incomplete")
        capability_id = entry["capability_id"]
        if capability_id in observed:
            raise ProviderHandoffError("provider capability is duplicated")
        observed.add(capability_id)
        if entry["mode"] not in ALLOWED_MODES or entry["status"] not in ALLOWED_STATUSES:
            raise ProviderHandoffError("provider mode or status is invalid")
        if entry["mode"] == "manual_handoff" and entry["external_write"] is not False:
            raise ProviderHandoffError("manual handoff cannot claim an external write")
        if entry["status"] == "connected" and entry["mode"] != "typed_adapter":
            raise ProviderHandoffError("connected provider requires a typed adapter")
    return catalog


def create_manual_handoff(
    *,
    catalog: Mapping[str, Any],
    capability_id: str,
    brand_id: str,
    campaign_id: str,
    paperclip_issue_id: str,
    approved_artifact_checksum: str,
    destination_ref: str,
) -> dict[str, Any]:
    entries = {
        item["capability_id"]: item
        for item in catalog.get("providers", [])
        if isinstance(item, Mapping)
    }
    entry = entries.get(capability_id)
    if entry is None or entry.get("mode") != "manual_handoff":
        raise ProviderHandoffError("manual provider handoff is not available")
    if not all(
        isinstance(value, str) and value
        for value in (
            brand_id,
            campaign_id,
            paperclip_issue_id,
            approved_artifact_checksum,
            destination_ref,
        )
    ):
        raise ProviderHandoffError("manual provider handoff is incomplete")
    return finalize_record(
        {
            "schema_version": "1.0",
            "artifact_type": "provider_manual_handoff",
            "handoff_id": f"{brand_id}:{campaign_id}:{capability_id}",
            "brand_id": brand_id,
            "campaign_id": campaign_id,
            "paperclip_issue_id": paperclip_issue_id,
            "capability_id": capability_id,
            "service_class": entry["service_class"],
            "mode": "manual_handoff",
            "status": "awaiting_operator",
            "approved_artifact_checksum": approved_artifact_checksum,
            "destination_ref": destination_ref,
            "operator_action": entry["operator_action"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "external_write_performed": False,
        }
    )
