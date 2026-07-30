from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agency_os.fleet_ingest_worker import FleetIngestError, process_source, process_spool
from agency_os.fleet_portal import (
    FleetPortalAuthority, FleetPortalAuthorizationError, SourceAdmissionPolicy,
    payload_checksum,
)
from agency_os import fleet_portal_authority_host
from agency_os.fleet_portal_authority_host import import_review_spool
from scripts.initialize_fleet_tenant import initialise as initialise_tenant
from agency_os.fleet_portal_command_worker import process_one


class _Board:
    approval_id = "4b0ba1a6-a311-4dc2-a639-8f8358ec2695"

    def __init__(self, *, fail: bool = False, fail_after_write: bool = False) -> None:
        self.fail = fail
        self.fail_after_write = fail_after_write
        self.calls: list[tuple[str, str, str, str | None]] = []
        self.approval = {
            "id": self.approval_id,
            "companyId": "d7e2e389-c7ad-486e-87ca-482e4ec6216d",
            "status": "pending",
            "payload": {"brand_id": "brand_fleet", "candidate_id": "candidate_worker"},
        }

    def get_approval(self, approval_id: str):
        if approval_id != self.approval_id:
            raise KeyError(approval_id)
        return dict(self.approval)

    def decide_approval(
        self, approval_id: str, *, decision: str, decision_note: str,
        idempotency_key: str | None = None,
    ):
        self.calls.append((approval_id, decision, decision_note, idempotency_key))
        if self.fail:
            from agency_os.integrations import IntegrationError
            raise IntegrationError("uncertain Paperclip outcome")
        self.approval["status"] = "approved" if decision == "approve" else "rejected"
        if self.fail_after_write:
            self.fail_after_write = False
            from agency_os.integrations import IntegrationError
            raise IntegrationError("Paperclip committed before transport failed")
        return dict(self.approval)


class FleetPortalWorkerTests(unittest.TestCase):
    def test_command_worker_operations_require_kernel_admitted_uid(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            authority = FleetPortalAuthority(Path(temporary) / "portal.sqlite3")
            request = {
                "operation": "claim_command", "worker_id": "worker",
                "brand_id": "brand_fleet",
            }
            original = fleet_portal_authority_host._CURRENT_WORKER_UIDS
            try:
                fleet_portal_authority_host._CURRENT_WORKER_UIDS = frozenset({7312})
                with self.assertRaises(FleetPortalAuthorizationError):
                    fleet_portal_authority_host._dispatch(authority, request, peer_uid=7311)
                self.assertIsNone(
                    fleet_portal_authority_host._dispatch(authority, request, peer_uid=7312)
                )
            finally:
                fleet_portal_authority_host._CURRENT_WORKER_UIDS = original

    def test_clean_text_source_becomes_review_required_not_approved(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.txt"
            metadata = root / "source.txt.json"
            output = root / "review"
            source.write_text("Fleet helps brands become AI ready.", encoding="utf-8")
            metadata.write_text(json.dumps({
                "source_id": "source_test", "original_filename": "brand.txt",
                "declared_content_type": "text/plain", "purpose": "Brand facts",
                "consent_basis": "owner supplied", "tenant_id": "tenant_fleet",
                "brand_id": "brand_fleet", "submitted_by": "owner_fleet",
                "correlation_id": "correlation_test",
            }), encoding="utf-8")
            record = process_source(source, metadata, output, scanner=lambda _path: None)
            self.assertEqual(record["state"], "review_required")
            self.assertEqual(record["inspection"]["malware_scan"], "clean")
            self.assertRegex(record["record_checksum"], r"^sha256:[a-f0-9]{64}$")
            saved = json.loads((output / "source_test.review.json").read_text())
            self.assertEqual(saved, record)

    def test_failed_malware_scan_emits_no_review_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.txt"
            metadata = root / "source.txt.json"
            output = root / "review"
            source.write_text("unsafe", encoding="utf-8")
            metadata.write_text(json.dumps({
                "source_id": "source_test", "original_filename": "brand.txt",
                "declared_content_type": "text/plain", "purpose": "Brand facts",
                "consent_basis": "owner supplied", "tenant_id": "tenant_fleet",
                "brand_id": "brand_fleet", "submitted_by": "owner_fleet",
                "correlation_id": "correlation_test",
            }), encoding="utf-8")
            def deny(_path: Path) -> None:
                raise FleetIngestError("malware")
            with self.assertRaises(FleetIngestError):
                process_source(source, metadata, output, scanner=deny)
            self.assertFalse(output.exists())

    def test_spool_only_processes_complete_pairs_and_moves_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            spool = Path(temporary)
            incoming = spool / "incoming"
            incoming.mkdir()
            source = incoming / "source_spool.txt"
            source.write_text("Fleet source evidence.", encoding="utf-8")
            Path(f"{source}.json").write_text(json.dumps({
                "source_id": "source_spool", "original_filename": "source.txt",
                "declared_content_type": "text/plain", "purpose": "Brand facts",
                "consent_basis": "owner supplied", "tenant_id": "tenant_fleet",
                "brand_id": "brand_fleet", "submitted_by": "owner_fleet",
                "correlation_id": "correlation_spool",
            }), encoding="utf-8")
            (incoming / "incomplete.txt").write_text("wait", encoding="utf-8")
            result = process_spool(spool, scanner=lambda _path: None)
            self.assertEqual(result, {"review_required": 1, "rejected": 0, "incomplete": 1})
            self.assertTrue((spool / "review/source_spool.review.json").is_file())
            self.assertTrue((spool / "processed/source_spool.txt").is_file())
            self.assertFalse(source.exists())

    def _queued_authority(self, root: Path) -> tuple[FleetPortalAuthority, object]:
        authority = FleetPortalAuthority(root / "portal.sqlite3")
        authority.register_membership(
            actor_id="admin", membership_id="membership_fleet",
            workos_subject="owner_fleet", workos_organization_id="org_fleet",
            customer_account_id="account_fleet", client_brand_id="client_brand_fleet",
            tenant_id="tenant_fleet", brand_id="brand_fleet", client_role="owner",
            approval_scopes=("brand_fact",), hostname="fleet.madebyfleet.com",
            entitlement_version=1,
        )
        context = authority.resolve_verified_identity(
            workos_subject="owner_fleet", workos_organization_id="org_fleet",
            hostname="fleet.madebyfleet.com", origin="https://fleet.madebyfleet.com",
            access_identity_verified=True, session_id="workos:owner_fleet",
            correlation_id="correlation_worker",
        )
        inspection = SourceAdmissionPolicy.inspect_upload(
            filename="brand.txt", declared_content_type="text/plain",
            content=b"Fleet helps brands become AI ready.", malware_clean=True,
        )
        source = authority.record_source(
            context, source_id="source_worker", inspection=inspection,
            purpose="Brand fact", consent_basis="owner supplied",
            visibility="client_and_fleet", sensitivity="internal",
        )
        candidate = authority.create_candidate_fact(
            context, candidate_id="candidate_worker", source_id=source["source_id"],
            source_locator="line 1", statement="Fleet helps brands become AI ready.",
        )
        authority.confirm_candidate(
            context, candidate_id=candidate["candidate_id"],
            expected_checksum=candidate["candidate_checksum"],
            statement=candidate["statement"],
        )
        pending_approval = _Board().approval
        approval_checksum = payload_checksum(pending_approval)
        authority.bind_paperclip_approval(
            actor_id="operator", tenant_id="tenant_fleet", brand_id="brand_fleet",
            approval_id=_Board.approval_id, approval_checksum=approval_checksum,
            candidate_id=candidate["candidate_id"],
        )
        authority.submit_command(
            context, command_id="command_decision", idempotency_key="idempotency_decision",
            command_type="paperclip_approval_decision",
            target_id=_Board.approval_id,
            expected_checksum=approval_checksum, approval_scope="brand_fact",
            payload={"decision": "approve", "decision_note": "Fleet owner confirmed."},
        )
        return authority, context

    def test_complete_authoritative_launch_room_to_brand_twin_journey(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            config = json.loads(
                (Path(__file__).resolve().parents[1] / "config/fleet-generation2.json").read_text()
            )
            tenant_database = root / "tenancy.sqlite3"
            initialise_tenant(config, tenant_database)
            authority = FleetPortalAuthority(
                root / "portal.sqlite3", tenant_authority_path=tenant_database,
            )
            authority.register_membership(
                actor_id="admin", membership_id="membership_e2e",
                workos_subject="owner_e2e",
                workos_organization_id="org_fleet_g26_acceptance",
                customer_account_id="account_fleet", client_brand_id="client_brand_fleet",
                tenant_id="tenant_fleet", brand_id="brand_fleet", client_role="owner",
                approval_scopes=("brand_fact",), hostname="fleet.madebyfleet.com",
                entitlement_version=1,
            )
            context = authority.resolve_verified_identity(
                workos_subject="owner_e2e",
                workos_organization_id="org_fleet_g26_acceptance",
                hostname="fleet.madebyfleet.com", origin="https://fleet.madebyfleet.com",
                access_identity_verified=True, session_id="workos:e2e",
                correlation_id="correlation_e2e",
            )
            spool = root / "spool"
            incoming = spool / "incoming"
            incoming.mkdir(parents=True)
            content = b"Fleet helps brands become AI ready."
            authority.reserve_source_upload(
                context, source_id="source_e2e", filename="brand.txt",
                size_bytes=len(content), purpose="Brand fact",
            )
            source_path = incoming / "source_e2e.txt"
            source_path.write_bytes(content)
            Path(f"{source_path}.json").write_text(json.dumps({
                "source_id": "source_e2e", "original_filename": "brand.txt",
                "declared_content_type": "text/plain", "purpose": "Brand fact",
                "consent_basis": "owner supplied", "tenant_id": "tenant_fleet",
                "brand_id": "brand_fleet", "submitted_by": "owner_e2e",
                "correlation_id": "correlation_e2e",
            }))
            self.assertEqual(
                process_spool(spool, scanner=lambda _path: None)["review_required"], 1,
            )
            self.assertEqual(import_review_spool(authority, spool / "review")["admitted"], 1)
            candidate = authority.list_candidates(context)[0]
            authority.confirm_candidate(
                context, candidate_id=candidate["candidate_id"],
                expected_checksum=candidate["candidate_checksum"],
                statement=candidate["statement"],
            )
            board = _Board()
            approval_checksum = payload_checksum(board.approval)
            authority.bind_paperclip_approval(
                actor_id="fleet_reviewer", tenant_id="tenant_fleet", brand_id="brand_fleet",
                approval_id=board.approval_id, approval_checksum=approval_checksum,
                candidate_id=candidate["candidate_id"],
            )
            authority.submit_command(
                context, command_id="command_e2e", idempotency_key="idempotency_e2e",
                command_type="paperclip_approval_decision", target_id=board.approval_id,
                expected_checksum=approval_checksum, approval_scope="brand_fact",
                payload={"decision": "approve", "decision_note": "Owner confirmed exact fact."},
            )
            result = process_one(authority, board, worker_id="worker", brand_id="brand_fleet")
            self.assertEqual(result["state"], "completed")
            projection = authority.portal_projection(context)
            self.assertEqual(projection["brand_twin_claims"][0]["candidate_id"], candidate["candidate_id"])
            self.assertEqual(projection["approvals"][0]["state"], "resolved")

    def test_worker_records_paperclip_then_completes_projection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            authority, context = self._queued_authority(Path(temporary))
            board = _Board()
            result = process_one(authority, board, worker_id="worker", brand_id="brand_fleet")
            self.assertEqual(result["state"], "completed")
            self.assertEqual(len(board.calls), 1)
            self.assertEqual(
                authority.command_projection(context, command_id="command_decision")["state"],
                "completed",
            )

    def test_uncertain_committed_outcome_reconciles_without_second_decision(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            authority, context = self._queued_authority(Path(temporary))
            board = _Board(fail_after_write=True)
            first = process_one(authority, board, worker_id="worker", brand_id="brand_fleet")
            self.assertEqual(first["state"], "unknown")
            second = process_one(authority, board, worker_id="worker", brand_id="brand_fleet")
            self.assertEqual(second["state"], "completed")
            self.assertEqual(len(board.calls), 1)
            projection = authority.portal_projection(context)
            self.assertEqual(len(projection["brand_twin_claims"]), 1)

    def test_brand_twin_materialization_is_retry_safe(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            authority, _context = self._queued_authority(Path(temporary))
            board = _Board()
            command = authority.claim_next_command(worker_id="worker", brand_id="brand_fleet")
            board.decide_approval(
                board.approval_id, decision="approve", decision_note="confirmed",
                idempotency_key=command["idempotency_key"],
            )
            authority.transition_command(
                worker_id="worker", tenant_id="tenant_fleet", brand_id="brand_fleet",
                command_id=command["command_id"], expected_state="dispatching",
                next_state="authority_recorded",
            )
            authority.transition_command(
                worker_id="worker", tenant_id="tenant_fleet", brand_id="brand_fleet",
                command_id=command["command_id"], expected_state="authority_recorded",
                next_state="projecting",
            )
            first = authority.materialize_approval_outcome(
                worker_id="worker", command_id=command["command_id"], approval=board.approval,
            )
            second = authority.materialize_approval_outcome(
                worker_id="worker", command_id=command["command_id"], approval=board.approval,
            )
            self.assertEqual(first, second)

    def test_uncertain_paperclip_outcome_is_never_reported_as_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            authority, context = self._queued_authority(Path(temporary))
            result = process_one(
                authority, _Board(fail=True), worker_id="worker", brand_id="brand_fleet",
            )
            self.assertEqual(result["state"], "unknown")
            self.assertEqual(
                authority.command_projection(context, command_id="command_decision")["state"],
                "unknown",
            )


if __name__ == "__main__":
    unittest.main()
