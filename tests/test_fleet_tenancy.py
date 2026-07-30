from __future__ import annotations

import copy
import json
import sqlite3
import stat
import tempfile
import unittest
from pathlib import Path

from agency_os.contracts import ContractError, canonical_bytes, canonical_checksum, finalize_record, verify_record
from agency_os.fleet_tenancy import (
    PRODUCT_MODULES,
    PROVISIONING_STEPS,
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
FIXED_TIME = "2026-07-28T12:00:00Z"


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
        portal = make_product_entitlement(
            entitlement_id="entitlement_fleet_portal",
            brand_id="brand_fleet",
            module="client_portal",
            issued_by=self.director.actor_id,
            issued_at=FIXED_TIME,
        )
        self.authority.grant_entitlement(self.director, portal)
        projection = self.authority.portal_read_model(self.reviewer, self.hostname["hostname"])
        self.assertEqual(projection["brand_id"], "brand_fleet")
        self.assertNotIn("paperclip_company_id", projection)
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
        self.assertEqual(self.authority.schema_version(), 3)

    def test_account_brand_model_is_immutable_and_client_safe(self) -> None:
        self._register_fleet()
        projection = self.authority.admit_account_brand(
            self.director,
            customer_account_id="account_fleet",
            account_name="Fleet",
            client_brand_id="client_brand_fleet",
            client_brand_name="Fleet",
            tenant_id="tenant_fleet",
            workos_organization_id="org_fleet_test",
        )
        self.assertEqual(projection["brand_id"], "brand_fleet")
        self.assertEqual(projection["lifecycle_state"], "provisioning")
        self.assertNotIn("paperclip_company_id", projection)
        self.assertEqual(
            self.authority.admit_account_brand(
                self.director,
                customer_account_id="account_fleet",
                account_name="Fleet",
                client_brand_id="client_brand_fleet",
                client_brand_name="Fleet",
                tenant_id="tenant_fleet",
                workos_organization_id="org_fleet_test",
            ),
            projection,
        )
        with self.assertRaises(ContractError):
            self.authority.admit_account_brand(
                self.director,
                customer_account_id="account_fleet",
                account_name="Renamed in place",
                client_brand_id="client_brand_fleet",
                client_brand_name="Fleet",
                tenant_id="tenant_fleet",
                workos_organization_id="org_fleet_test",
            )

    def test_lifecycle_is_versioned_and_rejects_skips(self) -> None:
        self._register_fleet()
        self.authority.admit_account_brand(
            self.director,
            customer_account_id="account_fleet",
            account_name="Fleet",
            client_brand_id="client_brand_fleet",
            client_brand_name="Fleet",
            tenant_id="tenant_fleet",
            workos_organization_id="org_fleet_test",
        )
        launch_ready = self.authority.transition_tenant_lifecycle(
            self.director,
            client_brand_id="client_brand_fleet",
            expected_version=1,
            next_state="launch_ready",
        )
        self.assertEqual(launch_ready["lifecycle_version"], 2)
        with self.assertRaises(ContractError):
            self.authority.transition_tenant_lifecycle(
                self.director,
                client_brand_id="client_brand_fleet",
                expected_version=2,
                next_state="active",
            )
        with self.assertRaises(ContractError):
            self.authority.transition_tenant_lifecycle(
                self.director,
                client_brand_id="client_brand_fleet",
                expected_version=1,
                next_state="assurance",
            )

    def test_active_requires_every_durable_provisioning_step(self) -> None:
        self._register_fleet()
        self.authority.admit_account_brand(
            self.director, customer_account_id="account_fleet", account_name="Fleet",
            client_brand_id="client_brand_fleet", client_brand_name="Fleet",
            tenant_id="tenant_fleet", workos_organization_id="org_fleet_test",
        )
        projection = self.authority.account_brand_projection(self.director)
        for next_state in ("launch_ready", "assurance"):
            projection = self.authority.transition_tenant_lifecycle(
                self.director, client_brand_id="client_brand_fleet",
                expected_version=projection["lifecycle_version"], next_state=next_state,
            )
        with self.assertRaises(ContractError):
            self.authority.transition_tenant_lifecycle(
                self.director, client_brand_id="client_brand_fleet",
                expected_version=projection["lifecycle_version"], next_state="active",
            )
        run = self.authority.start_provisioning(
            self.director, provisioning_run_id="provisioning_test",
            client_brand_id="client_brand_fleet",
        )
        for step in PROVISIONING_STEPS:
            run = self.authority.complete_provisioning_step(
                self.director, provisioning_run_id="provisioning_test", step_key=step,
                evidence_checksum=canonical_checksum({"step": step, "evidence": "test"}),
            )
        self.assertEqual(run["state"], "completed")
        active = self.authority.transition_tenant_lifecycle(
            self.director, client_brand_id="client_brand_fleet",
            expected_version=projection["lifecycle_version"], next_state="active",
        )
        self.assertEqual(active["lifecycle_state"], "active")

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

    def test_unknown_runtime_fields_are_rejected(self) -> None:
        unexpected = copy.deepcopy(self.tenant)
        unexpected["internal_notes"] = "must not cross the public authority boundary"
        unexpected = finalize_record(unexpected)
        with self.assertRaises(ContractError):
            self.authority.register_tenant(self.director, unexpected)

    def test_every_denied_mutation_is_audited(self) -> None:
        reviewer_tenant = copy.deepcopy(self.tenant)
        reviewer_tenant["created_by"] = self.reviewer.actor_id
        reviewer_tenant = finalize_record(reviewer_tenant)
        with self.assertRaises(FleetTenancyAuthorizationError):
            self.authority.register_tenant(self.reviewer, reviewer_tenant)

        wrong_actor = copy.deepcopy(self.tenant)
        wrong_actor["created_by"] = "different_actor"
        wrong_actor = finalize_record(wrong_actor)
        with self.assertRaises(FleetTenancyAuthorizationError):
            self.authority.register_tenant(self.director, wrong_actor)

        with self.assertRaises(FleetTenancyAuthorizationError):
            self.authority.register_tenant(self.other_director, self.tenant)
        fleet_outcomes = {event["outcome"] for event in self.authority.audit_events(self.reviewer)}
        other_outcomes = {event["outcome"] for event in self.authority.audit_events(self.other_director)}
        self.assertIn("DENY_ROLE", fleet_outcomes)
        self.assertIn("DENY_ACTOR", fleet_outcomes)
        self.assertIn("DENY_TENANT", other_outcomes)

    def test_atomic_bundle_rolls_back_every_binding_after_late_failure(self) -> None:
        self._register_fleet()
        invalid_version = make_product_entitlement(
            entitlement_id="entitlement_fleet_content_v2",
            brand_id="brand_fleet",
            module="content_engine",
            version=2,
            supersedes_entitlement_id="missing_v1",
            issued_by=self.director.actor_id,
            issued_at=FIXED_TIME,
        )
        with self.assertRaises(ContractError):
            self.authority.initialize_bundle(
                self.director, self.tenant, [self.hostname], [invalid_version],
            )
        with self.assertRaises(KeyError):
            self.authority.authorize_hostname(self.reviewer, self.hostname["hostname"])
        self.assertFalse(self.authority.module_enabled(self.reviewer, "content_engine"))
        outcomes = [event["outcome"] for event in self.authority.audit_events(self.reviewer)]
        self.assertIn("DENY_ATOMIC", outcomes)

    def test_entitlement_versions_suspend_replace_and_reject_gaps(self) -> None:
        self._register_fleet()
        self.authority.grant_entitlement(self.director, self.content_entitlement)
        self.authority.suspend_entitlement(self.director, "content_engine")
        self.assertFalse(self.authority.module_enabled(self.reviewer, "content_engine"))

        replacement = make_product_entitlement(
            entitlement_id="entitlement_fleet_content_v2",
            brand_id="brand_fleet",
            module="content_engine",
            version=2,
            supersedes_entitlement_id=self.content_entitlement["entitlement_id"],
            limits={"maximum_active_campaigns": 10},
            issued_by=self.director.actor_id,
            issued_at=FIXED_TIME,
        )
        self.authority.grant_entitlement(self.director, replacement)
        self.assertTrue(self.authority.module_enabled(self.reviewer, "content_engine"))

        gap = make_product_entitlement(
            entitlement_id="entitlement_fleet_content_v4",
            brand_id="brand_fleet",
            module="content_engine",
            version=4,
            supersedes_entitlement_id=replacement["entitlement_id"],
            issued_by=self.director.actor_id,
            issued_at=FIXED_TIME,
        )
        with self.assertRaises(ContractError):
            self.authority.grant_entitlement(self.director, gap)

    def test_future_and_expired_entitlements_fail_closed(self) -> None:
        self._register_fleet()
        future = make_product_entitlement(
            entitlement_id="entitlement_fleet_twin_future",
            brand_id="brand_fleet",
            module="brand_twin",
            issued_by=self.director.actor_id,
            issued_at=FIXED_TIME,
            effective_at="2099-01-01T00:00:00Z",
        )
        self.authority.grant_entitlement(self.director, future)
        self.assertFalse(self.authority.module_enabled(self.reviewer, "brand_twin"))

        expired = make_product_entitlement(
            entitlement_id="entitlement_fleet_measurement_expired",
            brand_id="brand_fleet",
            module="measurement",
            issued_by=self.director.actor_id,
            issued_at="2020-01-01T00:00:00Z",
            effective_at="2020-01-01T00:00:00Z",
            expires_at="2021-01-01T00:00:00Z",
        )
        self.authority.grant_entitlement(self.director, expired)
        self.assertFalse(self.authority.module_enabled(self.reviewer, "measurement"))

    def test_portal_projection_requires_its_own_entitlement(self) -> None:
        self._register_full_fleet()
        with self.assertRaises(FleetTenancyAuthorizationError):
            self.authority.portal_read_model(self.reviewer, self.hostname["hostname"])
        outcomes = [event["outcome"] for event in self.authority.audit_events(self.reviewer)]
        self.assertIn("DENY_ENTITLEMENT", outcomes)

    def test_v1_database_is_really_migrated_to_v2(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "legacy.sqlite3"
            database_path.touch(mode=0o600)
            database_path.chmod(0o600)
            old_entitlement = copy.deepcopy(self.content_entitlement)
            for field in (
                "version", "effective_at", "expires_at", "supersedes_entitlement_id",
            ):
                old_entitlement.pop(field)
            old_entitlement = finalize_record(old_entitlement)
            connection = sqlite3.connect(database_path)
            connection.executescript(
                """
                CREATE TABLE schema_metadata (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL);
                INSERT INTO schema_metadata VALUES (1, 1);
                CREATE TABLE brand_tenants (
                    brand_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL UNIQUE,
                    paperclip_company_id TEXT NOT NULL UNIQUE, record_json TEXT NOT NULL,
                    state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE portal_hostnames (
                    hostname TEXT PRIMARY KEY, brand_id TEXT NOT NULL,
                    binding_id TEXT NOT NULL UNIQUE, record_json TEXT NOT NULL,
                    state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    FOREIGN KEY (brand_id) REFERENCES brand_tenants(brand_id)
                );
                CREATE TABLE product_entitlements (
                    brand_id TEXT NOT NULL, module TEXT NOT NULL,
                    entitlement_id TEXT NOT NULL UNIQUE, record_json TEXT NOT NULL,
                    state TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    PRIMARY KEY (brand_id, module),
                    FOREIGN KEY (brand_id) REFERENCES brand_tenants(brand_id)
                );
                CREATE TABLE authority_audit (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT, actor_id TEXT NOT NULL,
                    role_id TEXT NOT NULL, brand_id TEXT NOT NULL, operation TEXT NOT NULL,
                    target_id TEXT NOT NULL, outcome TEXT NOT NULL, recorded_at TEXT NOT NULL
                );
                """
            )
            connection.execute(
                "INSERT INTO brand_tenants VALUES (?, ?, ?, ?, 'active', ?, ?)",
                (
                    self.tenant["brand_id"], self.tenant["tenant_id"],
                    self.tenant["paperclip_company_id"],
                    canonical_bytes(self.tenant).decode("utf-8"), FIXED_TIME, FIXED_TIME,
                ),
            )
            connection.execute(
                "INSERT INTO product_entitlements VALUES (?, ?, ?, ?, 'active', ?, ?)",
                (
                    old_entitlement["brand_id"], old_entitlement["module"],
                    old_entitlement["entitlement_id"],
                    canonical_bytes(old_entitlement).decode("utf-8"), FIXED_TIME, FIXED_TIME,
                ),
            )
            connection.commit()
            connection.close()

            migrated = FleetTenantAuthority(database_path)
            self.assertEqual(migrated.schema_version(), 3)
            self.assertTrue(migrated.module_enabled(self.reviewer, "content_engine"))
            connection = sqlite3.connect(database_path)
            row = connection.execute(
                "SELECT version, record_json FROM product_entitlements"
            ).fetchone()
            connection.close()
            self.assertEqual(row[0], 1)
            record = json.loads(row[1])
            self.assertEqual(record["effective_at"], "1970-01-01T00:00:00Z")
            self.assertIsNone(record["supersedes_entitlement_id"])
            outcomes = [event["operation"] for event in migrated.audit_events(self.reviewer)]
            self.assertIn("migrate_entitlement_v1_to_v2", outcomes)

    def test_checksum_tampering_is_rejected(self) -> None:
        tampered = copy.deepcopy(self.tenant)
        tampered["company_name"] = "Tampered"
        with self.assertRaises(ContractError):
            self.authority.register_tenant(self.director, tampered)


if __name__ == "__main__":
    unittest.main()
