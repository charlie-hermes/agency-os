from __future__ import annotations

import copy
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path

from agency_os.contracts import ContractError, finalize_record, verify_record
from agency_os.fleet_tenancy import (
    PRODUCT_MODULES,
    FleetTenancyAuthorizationError,
    FleetTenancyError,
    FleetTenantAuthority,
    make_brand_tenant,
    make_portal_hostname_binding,
    make_product_entitlement,
)
from agency_os.store import Principal


FLEET_COMPANY_ID = "d7e2e389-c7ad-486e-87ca-482e4ec6216d"
OTHER_COMPANY_ID = "63d47cf2-df2d-4fbb-88e7-d8db70bddcec"
FIXED_TIME = "2026-07-29T12:00:00Z"


class FleetTenantAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.database_path = Path(temporary_directory.name) / "fleet-tenancy.sqlite3"
        self.authority = FleetTenantAuthority(self.database_path)
        self.director = Principal("director_fleet", "agency-director", "brand_fleet")
        self.reviewer = Principal("reviewer_fleet", "platform-assurance-reviewer", "brand_fleet")
        self.other_director = Principal("director_other", "agency-director", "brand_other")
        self.tenant = make_brand_tenant(
            tenant_id="tenant_fleet",
            brand_id="brand_fleet",
            paperclip_company_id=FLEET_COMPANY_ID,
            company_name="Fleet DMA",
            created_by=self.director.actor_id,
            created_at=FIXED_TIME,
        )
        self.hostname = make_portal_hostname_binding(
            binding_id="hostname_fleet",
            brand_id="brand_fleet",
            brand_slug="internal-fleet",
            hostname="internal-fleet.madebyfleet.com",
            created_by=self.director.actor_id,
            created_at=FIXED_TIME,
        )
        self.content_entitlement = make_product_entitlement(
            entitlement_id="entitlement_fleet_content",
            brand_id="brand_fleet",
            module="content_engine",
            issued_by=self.director.actor_id,
            limits={},
            issued_at=FIXED_TIME,
        )

    def _register_fleet(self) -> None:
        self.authority.register_tenant(self.director, self.tenant)

    def _register_full_fleet(self) -> None:
        self._register_fleet()
        self.authority.register_hostname(self.director, self.hostname)
        self.authority.grant_entitlement(self.director, self.content_entitlement)

    def test_records_are_canonical_checksummed_contracts(self) -> None:
        for record in (self.tenant, self.hostname, self.content_entitlement):
            verify_record(record)
            self.assertEqual(record["schema_version"], "2.0")

    def test_only_same_brand_director_can_change_authority(self) -> None:
        reviewer_tenant = copy.deepcopy(self.tenant)
        reviewer_tenant["created_by"] = self.reviewer.actor_id
        reviewer_tenant = finalize_record(reviewer_tenant)
        with self.assertRaises(FleetTenancyAuthorizationError):
            self.authority.register_tenant(self.reviewer, reviewer_tenant)

        with self.assertRaises(FleetTenancyAuthorizationError):
            self.authority.register_tenant(self.other_director, self.tenant)

    def test_tenant_binding_is_idempotent_immutable_and_one_to_one(self) -> None:
        self.assertEqual(self.authority.register_tenant(self.director, self.tenant), "tenant_fleet")
        self.assertEqual(self.authority.register_tenant(self.director, self.tenant), "tenant_fleet")

        changed = copy.deepcopy(self.tenant)
        changed["company_name"] = "Changed"
        changed = finalize_record(changed)
        with self.assertRaises(ContractError):
            self.authority.register_tenant(self.director, changed)

        duplicate_company = make_brand_tenant(
            tenant_id="tenant_other",
            brand_id="brand_other",
            paperclip_company_id=FLEET_COMPANY_ID,
            company_name="Other",
            created_by=self.other_director.actor_id,
            created_at=FIXED_TIME,
        )
        with self.assertRaises(ContractError):
            self.authority.register_tenant(self.other_director, duplicate_company)

    def test_modules_are_independent_and_disabled_by_default(self) -> None:
        self._register_fleet()
        self.assertFalse(self.authority.module_enabled(self.reviewer, "content_engine"))
        self.assertFalse(self.authority.module_enabled(self.reviewer, "brand_twin"))
        self.assertFalse(self.authority.module_enabled(self.reviewer, "not_a_module"))

        self.authority.grant_entitlement(self.director, self.content_entitlement)
        twin = make_product_entitlement(
            entitlement_id="entitlement_fleet_twin",
            brand_id="brand_fleet",
            module="brand_twin",
            issued_by=self.director.actor_id,
            issued_at=FIXED_TIME,
        )
        self.authority.grant_entitlement(self.director, twin)
        self.assertTrue(self.authority.module_enabled(self.reviewer, "content_engine"))
        self.assertTrue(self.authority.module_enabled(self.reviewer, "brand_twin"))
        self.assertFalse(self.authority.module_enabled(self.reviewer, "client_portal"))

    def test_entitlement_suspension_fails_closed_without_affecting_other_modules(self) -> None:
        self._register_fleet()
        self.authority.grant_entitlement(self.director, self.content_entitlement)
        twin = make_product_entitlement(
            entitlement_id="entitlement_fleet_twin",
            brand_id="brand_fleet",
            module="brand_twin",
            issued_by=self.director.actor_id,
            issued_at=FIXED_TIME,
        )
        self.authority.grant_entitlement(self.director, twin)
        self.authority.suspend_entitlement(self.director, "brand_twin")
        self.assertTrue(self.authority.module_enabled(self.reviewer, "content_engine"))
        self.assertFalse(self.authority.module_enabled(self.reviewer, "brand_twin"))

    def test_tenant_suspension_disables_every_module_and_hostname(self) -> None:
        self._register_full_fleet()
        self.authority.suspend_tenant(self.director, "brand_fleet")
        self.assertFalse(self.authority.module_enabled(self.reviewer, "content_engine"))
        with self.assertRaises(KeyError):
            self.authority.authorize_hostname(self.reviewer, self.hostname["hostname"])

    def test_hostname_is_exact_and_cross_tenant_lookup_looks_absent(self) -> None:
        self._register_full_fleet()
        other_tenant = make_brand_tenant(
            tenant_id="tenant_other",
            brand_id="brand_other",
            paperclip_company_id=OTHER_COMPANY_ID,
            company_name="Other Brand",
            created_by=self.other_director.actor_id,
            created_at=FIXED_TIME,
        )
        other_host = make_portal_hostname_binding(
            binding_id="hostname_other",
            brand_id="brand_other",
            brand_slug="other-brand",
            hostname="other-brand.madebyfleet.com",
            created_by=self.other_director.actor_id,
            created_at=FIXED_TIME,
        )
        self.authority.register_tenant(self.other_director, other_tenant)
        self.authority.register_hostname(self.other_director, other_host)

        resolved = self.authority.authorize_hostname(
            self.reviewer, "INTERNAL-FLEET.MADEBYFLEET.COM."
        )
        self.assertEqual(resolved, self.hostname)
        with self.assertRaises(KeyError):
            self.authority.authorize_hostname(self.reviewer, other_host["hostname"])
        with self.assertRaises(KeyError):
            self.authority.authorize_hostname(self.reviewer, "unknown.madebyfleet.com")

    def test_portal_projection_is_server_built_and_safe(self) -> None:
        self._register_full_fleet()
        projection = self.authority.portal_read_model(self.reviewer, self.hostname["hostname"])
        self.assertEqual(projection["brand_id"], "brand_fleet")
        self.assertEqual(projection["paperclip_company_id"], FLEET_COMPANY_ID)
        self.assertTrue(projection["modules"]["content_engine"])
        self.assertFalse(projection["modules"]["brand_twin"])
        self.assertEqual(set(projection["modules"]), PRODUCT_MODULES)
        self.assertNotIn("content_checksum", projection)

    def test_restart_preserves_bindings_entitlements_and_audit(self) -> None:
        self._register_full_fleet()
        restarted = FleetTenantAuthority(self.database_path)
        self.assertEqual(restarted.get_tenant(self.reviewer), self.tenant)
        self.assertEqual(restarted.authorize_hostname(self.reviewer, self.hostname["hostname"]), self.hostname)
        self.assertTrue(restarted.module_enabled(self.reviewer, "content_engine"))
        self.assertGreaterEqual(len(restarted.audit_events(self.reviewer)), 6)

    def test_audit_is_brand_scoped(self) -> None:
        self._register_fleet()
        other_tenant = make_brand_tenant(
            tenant_id="tenant_other",
            brand_id="brand_other",
            paperclip_company_id=OTHER_COMPANY_ID,
            company_name="Other Brand",
            created_by=self.other_director.actor_id,
            created_at=FIXED_TIME,
        )
        self.authority.register_tenant(self.other_director, other_tenant)
        fleet_events = self.authority.audit_events(self.reviewer)
        other_events = self.authority.audit_events(self.other_director)
        self.assertTrue(fleet_events)
        self.assertTrue(other_events)
        self.assertEqual({event["brand_id"] for event in fleet_events}, {"brand_fleet"})
        self.assertEqual({event["brand_id"] for event in other_events}, {"brand_other"})

    def test_database_is_owner_only_and_has_migration_metadata(self) -> None:
        self.assertEqual(stat.S_IMODE(self.database_path.stat().st_mode), 0o600)
        self.assertEqual(self.authority.schema_version(), 1)

    def test_future_schema_version_is_rejected(self) -> None:
        connection = sqlite3.connect(self.database_path)
        connection.execute("UPDATE schema_metadata SET version = 999 WHERE id = 1")
        connection.commit()
        connection.close()
        with self.assertRaises(FleetTenancyError):
            FleetTenantAuthority(self.database_path)

    def test_group_writable_parent_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            unsafe_parent = Path(temporary_directory)
            unsafe_parent.chmod(0o770)
            with self.assertRaises(FleetTenancyError):
                FleetTenantAuthority(unsafe_parent / "fleet.sqlite3")

    def test_running_authority_rejects_database_identity_replacement(self) -> None:
        self._register_full_fleet()
        replacement_path = self.database_path.with_name("replacement.sqlite3")
        replacement = FleetTenantAuthority(replacement_path)
        replacement.register_tenant(self.director, self.tenant)
        replacement.register_hostname(self.director, self.hostname)
        replacement.grant_entitlement(self.director, self.content_entitlement)
        replacement_path.replace(self.database_path)

        with self.assertRaises(FleetTenancyError):
            self.authority.module_enabled(self.reviewer, "content_engine")

    def test_invalid_hosts_reserved_slugs_and_unknown_modules_are_rejected(self) -> None:
        with self.assertRaises(ContractError):
            make_portal_hostname_binding(
                binding_id="bad",
                brand_id="brand_fleet",
                brand_slug="admin",
                hostname="admin.madebyfleet.com",
                created_by=self.director.actor_id,
                created_at=FIXED_TIME,
            )
        with self.assertRaises(ContractError):
            make_portal_hostname_binding(
                binding_id="bad",
                brand_id="brand_fleet",
                brand_slug="fleet-demo",
                hostname="attacker.example.com",
                created_by=self.director.actor_id,
                created_at=FIXED_TIME,
            )
        with self.assertRaises(ContractError):
            make_product_entitlement(
                entitlement_id="bad",
                brand_id="brand_fleet",
                module="invented_module",
                issued_by=self.director.actor_id,
                issued_at=FIXED_TIME,
            )

    def test_checksum_tampering_is_rejected(self) -> None:
        tampered = copy.deepcopy(self.tenant)
        tampered["company_name"] = "Tampered"
        with self.assertRaises(ContractError):
            self.authority.register_tenant(self.director, tampered)


if __name__ == "__main__":
    unittest.main()
