#!/usr/bin/env python3
"""Read-only verification of the live Fleet Generation 2 foundation."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agency_os.fleet_tenancy import (
    PRODUCT_MODULES,
    FleetTenancyError,
    FleetTenantAuthority,
    _entitlement_is_effective,
    _validated_stored_record,
    make_brand_tenant,
    make_portal_hostname_binding,
    make_product_entitlement,
)
from agency_os.sqlite_storage import SQLiteStorageError, validate_sqlite_storage


def verify_foundation(
    config_path: Path, database_path: Path, company_id_file: Path,
) -> dict[str, Any]:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    pilot = config["internal_pilot"]
    company_id = company_id_file.read_text(encoding="utf-8").strip()
    if company_id != pilot["paperclip_company_id"]:
        raise FleetTenancyError("live Paperclip company ID does not match Fleet config")
    try:
        validate_sqlite_storage(database_path)
    except SQLiteStorageError as exc:
        raise FleetTenancyError("unsafe live Fleet authority storage") from exc

    connection = sqlite3.connect(
        f"file:{database_path}?mode=ro", uri=True, isolation_level=None,
    )
    try:
        connection.execute("PRAGMA query_only = ON")
        version = connection.execute(
            "SELECT version FROM schema_metadata WHERE id = 1"
        ).fetchone()
        if version != (2,):
            raise FleetTenancyError("live Fleet authority is not schema version 2")
        FleetTenantAuthority._validate_schema_v2(connection)

        tenant_row = connection.execute(
            "SELECT record_json, state FROM brand_tenants WHERE brand_id = ?",
            (pilot["brand_id"],),
        ).fetchone()
        if tenant_row is None or tenant_row[1] != "active":
            raise FleetTenancyError("Fleet tenant binding is absent or suspended")
        tenant = _validated_stored_record(
            tenant_row[0], "brand_tenant", pilot["brand_id"],
        )
        expected_tenant = make_brand_tenant(
            tenant_id=pilot["tenant_id"], brand_id=pilot["brand_id"],
            paperclip_company_id=pilot["paperclip_company_id"],
            company_name=pilot["paperclip_company_name"],
            created_by=pilot["agency_director_actor_id"], created_at=pilot["created_at"],
        )
        if tenant != expected_tenant:
            raise FleetTenancyError("live Fleet tenant record differs from config")

        expected_hostnames = {
            binding["hostname"]: make_portal_hostname_binding(
                binding_id=binding["binding_id"], brand_id=pilot["brand_id"],
                brand_slug=binding["brand_slug"], hostname=binding["hostname"],
                created_by=pilot["agency_director_actor_id"], created_at=pilot["created_at"],
            )
            for binding in config["hostname_bindings"]
        }
        hostname_rows = connection.execute(
            "SELECT hostname, record_json, state FROM portal_hostnames WHERE brand_id = ?",
            (pilot["brand_id"],),
        ).fetchall()
        observed_hostnames: dict[str, dict[str, Any]] = {}
        for hostname, record_json, state in hostname_rows:
            if state != "active":
                raise FleetTenancyError("configured Fleet hostname is suspended")
            observed_hostnames[hostname] = _validated_stored_record(
                record_json, "portal_hostname_binding", pilot["brand_id"],
            )
        if observed_hostnames != expected_hostnames:
            raise FleetTenancyError("live Fleet hostname bindings differ from config")

        expected_entitlements = {
            item["module"]: make_product_entitlement(
                entitlement_id=item["entitlement_id"], brand_id=pilot["brand_id"],
                module=item["module"], version=item["version"], limits=item.get("limits", {}),
                issued_by=pilot["agency_director_actor_id"], issued_at=item["issued_at"],
                effective_at=item["effective_at"], expires_at=item.get("expires_at"),
                supersedes_entitlement_id=item.get("supersedes_entitlement_id"),
            )
            for item in config["product_entitlements"]
        }
        enabled_modules: list[str] = []
        for module in sorted(PRODUCT_MODULES):
            row = connection.execute(
                """
                SELECT record_json, state FROM product_entitlements
                WHERE brand_id = ? AND module = ? ORDER BY version DESC LIMIT 1
                """,
                (pilot["brand_id"], module),
            ).fetchone()
            expected = expected_entitlements.get(module)
            if expected is None:
                if row is not None:
                    raise FleetTenancyError(f"unexpected Fleet module entitlement: {module}")
                continue
            if row is None or row[1] != "active":
                raise FleetTenancyError(f"configured Fleet module is not active: {module}")
            record = _validated_stored_record(
                row[0], "product_entitlement", pilot["brand_id"],
            )
            if record != expected or not _entitlement_is_effective(record):
                raise FleetTenancyError(f"live Fleet module differs or is ineffective: {module}")
            enabled_modules.append(module)
        if set(config["available_modules"]) != PRODUCT_MODULES:
            raise FleetTenancyError("Fleet module catalogue differs from runtime")
        configured_enabled_modules = sorted(expected_entitlements)
        if enabled_modules != configured_enabled_modules:
            raise FleetTenancyError("live Fleet modules differ from configured entitlements")
    finally:
        connection.close()

    return {
        "status": "pass",
        "schema_version": 2,
        "brand_id": pilot["brand_id"],
        "tenant_id": pilot["tenant_id"],
        "paperclip_company_id": company_id,
        "hostnames": sorted(expected_hostnames),
        "enabled_modules": enabled_modules,
        "disabled_modules": sorted(PRODUCT_MODULES.difference(enabled_modules)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--company-id-file", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify_foundation(args.config, args.database, args.company_id_file), sort_keys=True))


if __name__ == "__main__":
    main()
