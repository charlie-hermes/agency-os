from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agency_os.fleet_portal import (
    FleetPortalAuthority,
    FleetPortalAuthorizationError,
    FleetPortalError,
    PortalRequestContext,
    SourceAdmissionPolicy,
)


FUTURE = "2099-01-01T00:00:00Z"
CHECKSUM = "sha256:" + "a" * 64


class FleetPortalAuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database_path = Path(self.temporary_directory.name) / "portal.sqlite3"
        self.authority = FleetPortalAuthority(self.database_path)
        self._membership(
            membership_id="membership_fleet",
            subject="user_fleet",
            organization="org_fleet",
            tenant_id="tenant_fleet",
            brand_id="brand_fleet",
            hostname="fleet.madebyfleet.com",
        )
        self.authority.create_session(
            session_id="session_fleet",
            membership_id="membership_fleet",
            workos_subject="user_fleet",
            workos_organization_id="org_fleet",
            expires_at=FUTURE,
        )
        self.context = self.authority.build_request_context(
            session_id="session_fleet",
            hostname="fleet.madebyfleet.com",
            origin="https://fleet.madebyfleet.com",
            access_identity_verified=True,
            correlation_id="correlation_1",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def _membership(
        self,
        *,
        membership_id: str,
        subject: str,
        organization: str,
        tenant_id: str,
        brand_id: str,
        hostname: str,
    ) -> None:
        self.authority.register_membership(
            actor_id="fleet_admin",
            membership_id=membership_id,
            workos_subject=subject,
            workos_organization_id=organization,
            customer_account_id=f"account_{brand_id}",
            client_brand_id=f"client_{brand_id}",
            tenant_id=tenant_id,
            brand_id=brand_id,
            client_role="owner",
            approval_scopes=("brand_fact", "content", "access_change"),
            hostname=hostname,
            entitlement_version=1,
        )

    def test_context_is_derived_from_session_and_exact_host(self) -> None:
        self.assertEqual(self.context.tenant_id, "tenant_fleet")
        self.assertEqual(self.context.brand_id, "brand_fleet")
        with self.assertRaises(FleetPortalAuthorizationError):
            self.authority.build_request_context(
                session_id="session_fleet",
                hostname="other.madebyfleet.com",
                origin="https://other.madebyfleet.com",
                access_identity_verified=True,
                correlation_id="bad_host",
            )
        with self.assertRaises(FleetPortalAuthorizationError):
            self.authority.build_request_context(
                session_id="session_fleet",
                hostname="fleet.madebyfleet.com",
                origin="https://attacker.example",
                access_identity_verified=True,
                correlation_id="bad_origin",
            )
        with self.assertRaises(FleetPortalAuthorizationError):
            self.authority.build_request_context(
                session_id="session_fleet",
                hostname="fleet.madebyfleet.com",
                origin="https://fleet.madebyfleet.com",
                access_identity_verified=False,
                correlation_id="no_access",
            )

    def test_revocation_fails_closed(self) -> None:
        self.authority.revoke_session(session_id="session_fleet", actor_id="fleet_admin")
        with self.assertRaises(FleetPortalAuthorizationError):
            self.authority.build_request_context(
                session_id="session_fleet",
                hostname="fleet.madebyfleet.com",
                origin="https://fleet.madebyfleet.com",
                access_identity_verified=True,
                correlation_id="revoked",
            )

    def test_idle_and_absolute_session_limits_fail_closed(self) -> None:
        stale = (datetime.now(timezone.utc) - timedelta(minutes=61)).isoformat().replace("+00:00", "Z")
        connection = self.authority._connect()
        try:
            connection.execute(
                "UPDATE sessions SET last_seen_at = ? WHERE session_id = ?",
                (stale, "session_fleet"),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(FleetPortalAuthorizationError):
            self.authority.build_request_context(
                session_id="session_fleet", hostname="fleet.madebyfleet.com",
                origin="https://fleet.madebyfleet.com", access_identity_verified=True,
                correlation_id="idle_expired",
            )

        self.authority.create_session(
            session_id="session_absolute", membership_id="membership_fleet",
            workos_subject="user_fleet", workos_organization_id="org_fleet",
            expires_at=FUTURE,
        )
        old = (datetime.now(timezone.utc) - timedelta(hours=13)).isoformat().replace("+00:00", "Z")
        connection = self.authority._connect()
        try:
            connection.execute(
                "UPDATE sessions SET created_at = ? WHERE session_id = ?",
                (old, "session_absolute"),
            )
            connection.commit()
        finally:
            connection.close()
        with self.assertRaises(FleetPortalAuthorizationError):
            self.authority.build_request_context(
                session_id="session_absolute", hostname="fleet.madebyfleet.com",
                origin="https://fleet.madebyfleet.com", access_identity_verified=True,
                correlation_id="absolute_expired",
            )

    def test_upload_admission_checks_content_limits_and_malware(self) -> None:
        inspection = SourceAdmissionPolicy.inspect_upload(
            filename="brand.txt",
            declared_content_type="text/plain",
            content=b"Fleet is an AI brand operating system.",
            malware_clean=True,
        )
        self.assertEqual(inspection["detected_type"], "txt")
        with self.assertRaises(FleetPortalError):
            SourceAdmissionPolicy.inspect_upload(
                filename="brand.pdf",
                declared_content_type="application/pdf",
                content=b"not a PDF",
                malware_clean=True,
            )
        with self.assertRaises(FleetPortalError):
            SourceAdmissionPolicy.inspect_upload(
                filename="malware.txt",
                declared_content_type="text/plain",
                content=b"payload",
                malware_clean=False,
            )
        with self.assertRaises(FleetPortalError):
            SourceAdmissionPolicy.inspect_upload(
                filename="archive.zip",
                declared_content_type="application/zip",
                content=b"PK\x03\x04payload",
                malware_clean=True,
            )

    def test_url_admission_blocks_ssrf_and_credentials(self) -> None:
        self.assertEqual(
            SourceAdmissionPolicy.validate_url_hop(
                "https://www.example.com/brand", ("93.184.216.34",)
            ),
            "www.example.com",
        )
        for url, addresses in (
            ("http://example.com", ("93.184.216.34",)),
            ("https://user:secret@example.com", ("93.184.216.34",)),
            ("https://example.com:8443", ("93.184.216.34",)),
            ("https://example.com", ("127.0.0.1",)),
            ("https://example.com", ("169.254.169.254",)),
            ("https://example.com", ("::1",)),
        ):
            with self.subTest(url=url, addresses=addresses):
                with self.assertRaises(FleetPortalError):
                    SourceAdmissionPolicy.validate_url_hop(url, addresses)

    def test_source_candidate_command_and_catalogue_journey(self) -> None:
        inspection = SourceAdmissionPolicy.inspect_upload(
            filename="brand.txt",
            declared_content_type="text/plain",
            content=b"Fleet helps brands become AI ready.",
            malware_clean=True,
        )
        source = self.authority.record_source(
            self.context,
            source_id="source_fleet_1",
            inspection=inspection,
            purpose="Establish an approved brand fact",
            consent_basis="Fleet owner supplied",
            visibility="client_and_fleet",
            sensitivity="internal",
        )
        candidate = self.authority.create_candidate_fact(
            self.context,
            candidate_id="candidate_fleet_1",
            source_id=source["source_id"],
            source_locator="line 1",
            statement="Fleet helps brands become AI ready.",
        )
        command = self.authority.submit_command(
            self.context,
            command_id="command_fleet_1",
            idempotency_key="idem_fleet_1",
            command_type="approve_brand_fact",
            target_id=candidate["candidate_id"],
            expected_checksum=candidate["candidate_checksum"],
            approval_scope="brand_fact",
            payload={"decision": "approve", "note": "Confirmed by Fleet owner"},
        )
        self.assertEqual(command["state"], "received")
        replay = self.authority.submit_command(
            self.context,
            command_id="command_fleet_1",
            idempotency_key="idem_fleet_1",
            command_type="approve_brand_fact",
            target_id=candidate["candidate_id"],
            expected_checksum=candidate["candidate_checksum"],
            approval_scope="brand_fact",
            payload={"decision": "approve", "note": "Confirmed by Fleet owner"},
        )
        self.assertEqual(replay, command)
        for expected, next_state, receipt in (
            ("received", "dispatching", None),
            ("dispatching", "authority_recorded", {"paperclip_decision_id": "decision_1"}),
            ("authority_recorded", "projecting", None),
            ("projecting", "completed", None),
        ):
            self.authority.transition_command(
                worker_id="fleet_command_worker",
                tenant_id="tenant_fleet",
                brand_id="brand_fleet",
                command_id="command_fleet_1",
                expected_state=expected,
                next_state=next_state,
                authority_receipt=receipt,
            )
        self.assertEqual(
            self.authority.command_projection(self.context, command_id="command_fleet_1")["state"],
            "completed",
        )
        item = self.authority.add_content_item(
            actor_id="fleet_operator",
            content_id="content_fleet_controlled_1",
            tenant_id="tenant_fleet",
            brand_id="brand_fleet",
            title="Fleet AI readiness introduction",
            content_type="article",
            lifecycle_state="controlled_preview",
            source_checksum=source["record_checksum"],
        )
        self.assertEqual(self.authority.list_content(self.context)[0]["content_id"], item["content_id"])

    def test_two_tenants_with_same_resource_labels_remain_isolated(self) -> None:
        self._membership(
            membership_id="membership_other",
            subject="user_other",
            organization="org_other",
            tenant_id="tenant_other",
            brand_id="brand_other",
            hostname="other.madebyfleet.com",
        )
        self.authority.create_session(
            session_id="session_other",
            membership_id="membership_other",
            workos_subject="user_other",
            workos_organization_id="org_other",
            expires_at=FUTURE,
        )
        other = self.authority.build_request_context(
            session_id="session_other",
            hostname="other.madebyfleet.com",
            origin="https://other.madebyfleet.com",
            access_identity_verified=True,
            correlation_id="correlation_other",
        )
        for context, tenant, brand in (
            (self.context, "tenant_fleet", "brand_fleet"),
            (other, "tenant_other", "brand_other"),
        ):
            self.authority.add_content_item(
                actor_id="operator",
                content_id=f"shared-label-{brand}",
                tenant_id=tenant,
                brand_id=brand,
                title="Identical display title",
                content_type="article",
                lifecycle_state="draft",
                source_checksum=CHECKSUM,
            )
        self.assertEqual(len(self.authority.list_content(self.context)), 1)
        self.assertEqual(len(self.authority.list_content(other)), 1)
        with self.assertRaises(FleetPortalError):
            self.authority.transition_command(
                worker_id="worker",
                tenant_id="tenant_other",
                brand_id="brand_other",
                command_id="command_that_is_not_other",
                expected_state="received",
                next_state="dispatching",
            )

    def test_idempotency_key_cannot_be_reused_with_new_payload(self) -> None:
        self.authority.submit_command(
            self.context,
            command_id="command_1",
            idempotency_key="same_key",
            command_type="approve_brand_fact",
            target_id="candidate_1",
            expected_checksum=CHECKSUM,
            approval_scope="brand_fact",
            payload={"decision": "approve"},
        )
        with self.assertRaises(FleetPortalError):
            self.authority.submit_command(
                self.context,
                command_id="command_2",
                idempotency_key="same_key",
                command_type="approve_brand_fact",
                target_id="candidate_1",
                expected_checksum=CHECKSUM,
                approval_scope="brand_fact",
                payload={"decision": "reject"},
            )

    def test_paperclip_decision_requires_exact_pending_snapshot(self) -> None:
        approval_id = "4b0ba1a6-a311-4dc2-a639-8f8358ec2695"
        with self.assertRaises(FleetPortalError):
            self.authority.submit_command(
                self.context, command_id="command_unbound", idempotency_key="unbound",
                command_type="paperclip_approval_decision", target_id=approval_id,
                expected_checksum=CHECKSUM, approval_scope="brand_fact",
                payload={"decision": "approve", "decision_note": "Confirmed"},
            )
        self.authority.bind_paperclip_approval(
            actor_id="operator", tenant_id="tenant_fleet", brand_id="brand_fleet",
            approval_id=approval_id, approval_checksum=CHECKSUM,
        )
        with self.assertRaises(FleetPortalError):
            self.authority.submit_command(
                self.context, command_id="command_stale", idempotency_key="stale",
                command_type="paperclip_approval_decision", target_id=approval_id,
                expected_checksum="sha256:" + "b" * 64, approval_scope="brand_fact",
                payload={"decision": "approve", "decision_note": "Confirmed"},
            )


if __name__ == "__main__":
    unittest.main()
