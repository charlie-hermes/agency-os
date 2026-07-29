from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agency_os.fleet_ingest_worker import FleetIngestError, process_source, process_spool
from agency_os.fleet_portal import FleetPortalAuthority
from agency_os.fleet_portal_command_worker import process_one


class _Board:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls: list[tuple[str, str, str]] = []

    def decide_approval(self, approval_id: str, *, decision: str, decision_note: str):
        self.calls.append((approval_id, decision, decision_note))
        if self.fail:
            from agency_os.integrations import IntegrationError
            raise IntegrationError("uncertain Paperclip outcome")
        return {"id": approval_id, "status": "approved" if decision == "approve" else "rejected"}


class FleetPortalWorkerTests(unittest.TestCase):
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
        authority.bind_paperclip_approval(
            actor_id="operator", tenant_id="tenant_fleet", brand_id="brand_fleet",
            approval_id="4b0ba1a6-a311-4dc2-a639-8f8358ec2695",
            approval_checksum="sha256:" + "a" * 64,
        )
        authority.submit_command(
            context, command_id="command_decision", idempotency_key="idempotency_decision",
            command_type="paperclip_approval_decision",
            target_id="4b0ba1a6-a311-4dc2-a639-8f8358ec2695",
            expected_checksum="sha256:" + "a" * 64, approval_scope="brand_fact",
            payload={"decision": "approve", "decision_note": "Fleet owner confirmed."},
        )
        return authority, context

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
