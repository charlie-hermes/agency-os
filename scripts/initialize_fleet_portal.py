#!/usr/bin/env python3
"""Idempotently admit the single G2.6 Fleet DMA portal owner and catalogue."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agency_os.fleet_portal import FleetPortalAuthority


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--workos-organization-id", required=True)
    parser.add_argument("--workos-subject", required=True)
    parser.add_argument("--hostname", default="fleet.madebyfleet.com")
    args = parser.parse_args()
    authority = FleetPortalAuthority(args.database)
    authority.register_membership(
        actor_id="fleet_platform_administrator",
        membership_id="membership_fleet_owner_g26",
        workos_subject=args.workos_subject,
        workos_organization_id=args.workos_organization_id,
        customer_account_id="account_fleet", client_brand_id="client_brand_fleet",
        tenant_id="tenant_fleet", brand_id="brand_fleet", client_role="owner",
        approval_scopes=(
            "brand_fact", "claim", "content", "publication", "access_change",
        ),
        hostname=args.hostname, entitlement_version=1,
    )
    source_path = ROOT / "docs/fleet-generation2-decisions.md"
    source_checksum = "sha256:" + hashlib.sha256(source_path.read_bytes()).hexdigest()
    item = authority.add_content_item(
        actor_id="fleet_platform_administrator",
        content_id="content_fleet_controlled_g26_1",
        tenant_id="tenant_fleet", brand_id="brand_fleet",
        title="Fleet AI readiness introduction", content_type="article",
        lifecycle_state="controlled_preview",
        source_checksum=source_checksum,
    )
    print(json.dumps({
        "status": "admitted", "production_tenants": 1,
        "tenant_id": "tenant_fleet", "brand_id": "brand_fleet",
        "hostname": args.hostname, "content_id": item["content_id"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
