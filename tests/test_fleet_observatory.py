from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from agency_os.brand_intelligence import BrandIntelligenceAuthority, make_generation2_record
from agency_os.contracts import ContractError, finalize_record
from agency_os.fleet_brand_runtime import (
    build_fleet_records, claim_approval_package,
    initialise_fleet_brand_intelligence, load_fleet_brand_config,
)
from agency_os.fleet_observatory import run_fleet_observatory
from agency_os.store import Principal

ROOT = Path(__file__).resolve().parents[1]


class FleetObservatoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "intelligence.sqlite3"
        self.config = load_fleet_brand_config(ROOT / "config/fleet-brand-intelligence.json")
        records = build_fleet_records(self.config, ROOT)
        package = claim_approval_package(records)
        approval = {
            "id": "10000000-0000-4000-8000-000000000155", "status": "approved",
            "payload": copy.deepcopy(package), "updatedAt": "2026-07-29T12:00:00Z",
        }
        initialise_fleet_brand_intelligence(
            self.config, ROOT, self.database, approval, approved_by="human_owner",
        )
        self.authority = BrandIntelligenceAuthority(self.database)
        actors = self.config["actors"]
        self.director = Principal(actors["director"], "agency-director", "brand_fleet")
        self.analyst = Principal(actors["analyst"], "growth-intelligence-analyst", "brand_fleet")
        self.reviewer = Principal("reviewer", "platform-assurance-reviewer", "brand_fleet")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _run(self) -> dict[str, object]:
        return run_fleet_observatory(
            self.authority,
            evidence_path=ROOT / "evidence/fleet-public-search-baseline.json",
            repository_root=ROOT, director=self.director, analyst=self.analyst,
            reviewer=self.reviewer,
            paperclip_issue_id="b256f7b2-a4cf-488d-af90-d2aa66f8e895",
        )

    def test_twenty_missions_have_two_runs_and_honest_finding(self) -> None:
        first = self._run()
        second = self._run()
        self.assertEqual(first["complete_run_count"], 2)
        self.assertEqual(first["observation_count"], 40)
        self.assertEqual(first["finding_count"], 1)
        self.assertEqual(first["finding_checksum"], second["finding_checksum"])
        self.assertEqual(first["external_ai_coverage"], "unknown")
        self.assertTrue(first["knowns"])
        self.assertTrue(first["inferences"])
        self.assertTrue(first["unknowns"])

    def test_observation_rejects_unregistered_variant_and_direct_weak_finding(self) -> None:
        self._run()
        observation = self.authority.get(
            self.reviewer, "observation", "observation_fleet_001_001",
        )
        changed = copy.deepcopy(observation)
        changed["observation_id"] = "observation_unregistered_variant"
        changed["variant"] = "an unregistered prompt"
        changed = finalize_record(changed)
        with self.assertRaisesRegex(ContractError, "versioned mission"):
            self.authority.put(self.analyst, changed)
        source = self.authority.get(
            self.reviewer, "brand_source", "source_fleet_public_search_baseline",
        )
        from agency_os.brand_intelligence import source_reference
        weak = make_generation2_record(
            "market_finding", brand_id="brand_fleet", created_by=self.analyst.actor_id,
            provenance=[source_reference(source)], created_at="2026-07-29T12:04:18Z",
            effective_at="2026-07-29T12:04:18Z", finding_id="finding_direct_bypass",
            mission_ids=["mission_fleet_001"],
            observation_refs=["observation_fleet_001_001"],
            finding="One result cannot support a repeated finding.", classification="content_gap",
            confidence=1.0, knowns=["one observation"], inferences=[], unknowns=["repeat"],
            paperclip_issue_id="b256f7b2-a4cf-488d-af90-d2aa66f8e895", status="active",
        )
        with self.assertRaisesRegex(ContractError, "two runs"):
            self.authority.put(self.analyst, weak)


if __name__ == "__main__":
    unittest.main()
