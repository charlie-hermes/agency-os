from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from agency_os.brand_intelligence import BrandIntelligenceAuthority
from agency_os.contracts import ContractError
from agency_os.fictional_platforms import (
    InMemoryBuzzTransport, InMemoryPaperclipBoardTransport, InMemoryPaperclipTransport,
)
from agency_os.fleet_brand_runtime import (
    build_fleet_records, claim_approval_package, initialise_fleet_brand_intelligence,
    load_fleet_brand_config,
)
from agency_os.fleet_closed_loop import (
    build_remediation_proposal, remediation_approval_package, run_fleet_closed_loop,
)
from agency_os.fleet_observatory import run_fleet_observatory
from agency_os.gateway import MockPublisher
from agency_os.integrations import (
    PaperclipBoardApprovalAdapter, PaperclipBrandBinding, PaperclipLifecycleAdapter,
    TypedBuzzAdapter,
)
from agency_os.store import Principal
from scripts.verify_fleet_brand_intelligence import verify_live_intelligence

ROOT = Path(__file__).resolve().parents[1]


class FleetClosedLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "intelligence.sqlite3"
        self.config = load_fleet_brand_config(ROOT / "config/fleet-brand-intelligence.json")
        records = build_fleet_records(self.config, ROOT)
        package = claim_approval_package(records)
        claim_approval = {
            "id": "10000000-0000-4000-8000-000000000155", "status": "approved",
            "payload": copy.deepcopy(package), "updatedAt": "2026-07-29T12:00:00Z",
        }
        initialise_fleet_brand_intelligence(
            self.config, ROOT, self.database, claim_approval, approved_by="human_owner",
        )
        self.authority = BrandIntelligenceAuthority(self.database)
        actors = self.config["actors"]
        self.director = Principal(actors["director"], "agency-director", "brand_fleet")
        self.analyst = Principal(actors["analyst"], "growth-intelligence-analyst", "brand_fleet")
        self.reader = Principal("reviewer", "platform-assurance-reviewer", "brand_fleet")
        run_fleet_observatory(
            self.authority, evidence_path=ROOT / "evidence/fleet-public-search-baseline.json",
            repository_root=ROOT, director=self.director, analyst=self.analyst,
            reviewer=self.reader, paperclip_issue_id="b256f7b2-a4cf-488d-af90-d2aa66f8e895",
        )
        binding = PaperclipBrandBinding(
            "d7e2e389-c7ad-486e-87ca-482e4ec6216d", "brand_fleet",
        )
        self.transport = InMemoryPaperclipTransport(
            company_id=binding.company_id, brand_id=binding.brand_id,
        )
        self.paperclip = PaperclipLifecycleAdapter(self.transport, binding)
        self.board = PaperclipBoardApprovalAdapter(
            InMemoryPaperclipBoardTransport(self.transport), binding,
        )
        self.buzz = TypedBuzzAdapter(InMemoryBuzzTransport(), "brand_fleet")
        self.proposal = build_remediation_proposal(
            self.authority, director=self.director,
            finding_id="finding_fleet_public_explainer_gap_v1",
            paperclip_issue_id="b256f7b2-a4cf-488d-af90-d2aa66f8e895",
        )
        finding = self.authority.get(
            self.reader, "market_finding", "finding_fleet_public_explainer_gap_v1",
        )
        self.remediation_package = remediation_approval_package(self.proposal, finding)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_complete_approved_loop_uses_eight_tasks_and_one_mock_write(self) -> None:
        remediation_approval = {
            "id": "10000000-0000-4000-8000-000000000158", "status": "approved",
            "payload": copy.deepcopy(self.remediation_package),
            "updatedAt": "2026-07-29T12:10:00Z",
        }
        def approve_publication(requested, _manifest):
            return self.board.decide_approval(
                requested["id"], decision="approve",
                decision_note="Owner approved the exact controlled Fleet preview.",
            )
        publisher = MockPublisher(
            destination_ref="mock_preview:fleet", endpoint="mock://preview/fleet",
            expected_credential="fictional-credential-lantern",
        )
        result = run_fleet_closed_loop(
            self.authority, director=self.director, analyst=self.analyst,
            reader=self.reader, paperclip=self.paperclip, buzz=self.buzz,
            publication_approval_authority=approve_publication,
            remediation_approval=remediation_approval, approved_by="human_owner",
            paperclip_issue_id="b256f7b2-a4cf-488d-af90-d2aa66f8e895",
            publisher=publisher,
        )
        self.assertEqual(result["paperclip_task_count"], 8)
        self.assertTrue(result["paperclip_tasks_done"])
        self.assertEqual(result["publisher_calls"], 1)
        self.assertFalse(result["external_writes"])
        self.assertIn("No public-search", result["unknown"])
        published_claims = result["workflow"].records["published_draft"]["payload"]["claim_register"]
        self.assertEqual(
            {item["claim_id"] for item in published_claims},
            {"claim_fleet_business_name", "claim_fleet_unified_product", "claim_content_engine_first_class", "claim_paperclip_authority", "claim_real_providers_unconnected"},
        )
        self.assertEqual(
            self.authority.get(self.reader, "outcome_event", "outcome_fleet_controlled_preview_v1")["causal_status"],
            "observed",
        )
        before = len(self.authority.audit_events(self.reader))
        verified = verify_live_intelligence(self.database)
        after = len(self.authority.audit_events(self.reader))
        self.assertEqual(verified["record_counts"]["observation"], 40)
        self.assertEqual(verified["launch_mission_count"], 20)
        self.assertEqual(verified["external_ai_coverage"], "unknown")
        self.assertEqual(before, after)

    def test_rejected_or_altered_remediation_creates_no_work(self) -> None:
        for mode in ("rejected", "altered"):
            with self.subTest(mode=mode):
                approval = {
                    "id": f"approval_{mode}", "status": "approved",
                    "payload": copy.deepcopy(self.remediation_package),
                    "updatedAt": "2026-07-29T12:10:00Z",
                }
                if mode == "rejected":
                    approval["status"] = "rejected"
                else:
                    approval["payload"]["destination_class"] = "public_cms"
                with self.assertRaisesRegex(ContractError, "exact Fleet remediation"):
                    run_fleet_closed_loop(
                        self.authority, director=self.director, analyst=self.analyst,
                        reader=self.reader, paperclip=self.paperclip, buzz=self.buzz,
                        publication_approval_authority=lambda *_: None,
                        remediation_approval=approval, approved_by="human_owner",
                        paperclip_issue_id="b256f7b2-a4cf-488d-af90-d2aa66f8e895",
                    )
                self.assertEqual(self.transport.issues, {})


if __name__ == "__main__":
    unittest.main()
