#!/usr/bin/env python3
"""Idempotently initialise the protected Fleet DMA Generation 2 authority."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agency_os.fleet_tenancy import (
    PRODUCT_MODULES,
    FleetTenantAuthority,
    make_brand_tenant,
    make_portal_hostname_binding,
    make_product_entitlement,
)
from agency_os.store import Principal


def load_config(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.get("schema_version") != "2.0":
        raise ValueError("Fleet Generation 2 config must be a version 2.0 object")
    pilot = value.get("internal_pilot")
    if not isinstance(pilot, dict):
        raise ValueError("Fleet Generation 2 config has no internal_pilot")
    configured_modules = value.get("available_modules")
    if set(configured_modules or []) != PRODUCT_MODULES:
        raise ValueError("configured module catalogue does not match the runtime")
    return value


def initialise(
    config: dict[str, Any],
    database_path: Path,
    *,
    workos_organization_id: str | None = None,
) -> dict[str, Any]:
    pilot = config["internal_pilot"]
    director = Principal(
        actor_id=pilot["agency_director_actor_id"],
        role_id="agency-director",
        brand_id=pilot["brand_id"],
    )
    tenant = make_brand_tenant(
        tenant_id=pilot["tenant_id"],
        brand_id=pilot["brand_id"],
        paperclip_company_id=pilot["paperclip_company_id"],
        company_name=pilot["paperclip_company_name"],
        created_by=director.actor_id,
        created_at=pilot["created_at"],
    )
    hostnames = [
        make_portal_hostname_binding(
            binding_id=binding["binding_id"],
            brand_id=pilot["brand_id"],
            brand_slug=binding["brand_slug"],
            hostname=binding["hostname"],
            created_by=director.actor_id,
            created_at=pilot["created_at"],
        )
        for binding in config.get("hostname_bindings", [])
    ]
    entitlements = [
        make_product_entitlement(
            entitlement_id=entitlement["entitlement_id"],
            brand_id=pilot["brand_id"],
            module=entitlement["module"],
            version=entitlement["version"],
            limits=entitlement.get("limits", {}),
            issued_by=director.actor_id,
            issued_at=entitlement["issued_at"],
            effective_at=entitlement["effective_at"],
            expires_at=entitlement.get("expires_at"),
            supersedes_entitlement_id=entitlement.get("supersedes_entitlement_id"),
        )
        for entitlement in config.get("product_entitlements", [])
    ]

    authority = FleetTenantAuthority(database_path)
    authority.initialize_bundle(director, tenant, hostnames, entitlements)
    account = config["customer_account"]
    organisation = workos_organization_id or account["test_workos_organization_id"]
    account_projection = authority.admit_account_brand(
        director,
        customer_account_id=account["customer_account_id"],
        account_name=account["account_name"],
        client_brand_id=account["client_brand_id"],
        client_brand_name=account["client_brand_name"],
        tenant_id=pilot["tenant_id"],
        workos_organization_id=organisation,
        lifecycle_state=account["lifecycle_state"],
    )
    enabled_modules = {record["module"] for record in entitlements}
    return {
        "schema_version": authority.schema_version(),
        "tenant_id": tenant["tenant_id"],
        "brand_id": tenant["brand_id"],
        "paperclip_company_id": tenant["paperclip_company_id"],
        "hostnames": sorted(record["hostname"] for record in hostnames),
        "enabled_modules": sorted(enabled_modules),
        "disabled_modules": sorted(PRODUCT_MODULES.difference(enabled_modules)),
        "account": account_projection,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("config/fleet-generation2.json"))
    parser.add_argument("--database", type=Path)
    parser.add_argument("--assert-company-id-file", type=Path)
    args = parser.parse_args()

    config = load_config(args.config)
    database_path = args.database or Path(config["authority_database"])
    expected_company_id = config["internal_pilot"]["paperclip_company_id"]
    production_database = Path(config["authority_database"]).resolve()
    if database_path.resolve() == production_database and args.assert_company_id_file is None:
        raise SystemExit("production initialization requires --assert-company-id-file")
    if args.assert_company_id_file is not None:
        observed_company_id = args.assert_company_id_file.read_text(encoding="utf-8").strip()
        if observed_company_id != expected_company_id:
            raise SystemExit("live Paperclip company ID does not match the Fleet binding")

    production_identity: str | None = None
    if database_path.resolve() == production_database:
        env_name = config["customer_account"]["workos_organization_id_env"]
        production_identity = os.environ.get(env_name)
        if not production_identity:
            raise SystemExit(f"production initialization requires {env_name}")

    result = initialise(
        config,
        database_path,
        workos_organization_id=production_identity,
    )
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
