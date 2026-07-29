from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from agency_os.brand_intelligence import BrandIntelligenceAuthority
from agency_os.contracts import ContractError
from agency_os.fleet_brand_runtime import (
    build_fleet_records,
    claim_approval_package,
    initialise_fleet_brand_intelligence,
    load_fleet_brand_config,
)
from agency_os.store import Principal

ROOT = Path(__file__).resolve().parents[1]


class FleetBrandRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary.name) / "intelligence.sqlite3"
        self.config = load_fleet_brand_config(ROOT / "config/fleet-brand-intelligence.json")
        self.records = build_fleet_records(self.config, ROOT)
        self.package = claim_approval_package(self.records)
        self.approval = {
            "id": "10000000-0000-4000-8000-000000000155",
            "status": "approved",
            "payload": copy.deepcopy(self.package),
            "decisionNote": "Owner approved Fleet Brand Twin claim package v1.",
            "updatedAt": "2026-07-29T12:00:00Z",
        }

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def test_initialises_approved_twin_and_fifty_missions_idempotently(self) -> None:
        first = initialise_fleet_brand_intelligence(
            self.config, ROOT, self.database, self.approval, approved_by="human_owner",
        )
        second = initialise_fleet_brand_intelligence(
            self.config, ROOT, self.database, self.approval, approved_by="human_owner",
        )
        self.assertEqual(first["approved_claim_count"], 8)
        self.assertEqual(first["mission_count"], 50)
        self.assertEqual(first["launch_mission_count"], 20)
        self.assertEqual(first["profile_checksum"], second["profile_checksum"])
        authority = BrandIntelligenceAuthority(self.database)
        reader = Principal("reviewer", "platform-assurance-reviewer", "brand_fleet")
        profile = authority.operating_profile(reader)
        self.assertEqual(profile["conflicts"], [])
        self.assertEqual(profile["evidence_gaps"], [])
        self.assertEqual(len(profile["sources"]), 3)
        self.assertEqual(len(profile["entities"]), 8)
        self.assertEqual(len(profile["policies"]), 6)

    def test_altered_or_pending_approval_creates_no_authority(self) -> None:
        for state in ("pending", "altered"):
            with self.subTest(state=state):
                approval = copy.deepcopy(self.approval)
                if state == "pending":
                    approval["status"] = "pending"
                else:
                    approval["payload"]["brand_id"] = "brand_other"
                target = Path(self.temporary.name) / f"{state}.sqlite3"
                with self.assertRaisesRegex(ContractError, "exact Fleet Brand Twin"):
                    initialise_fleet_brand_intelligence(
                        self.config, ROOT, target, approval, approved_by="human_owner",
                    )
                self.assertFalse(target.exists())

    def test_source_bytes_and_approval_package_are_checksum_bound(self) -> None:
        self.assertEqual(len(self.package["claims"]), 8)
        self.assertEqual(len(self.package["policies"]), 6)
        changed = copy.deepcopy(self.package)
        changed["claims"][0]["checksum"] = "sha256:" + "0" * 64
        approval = copy.deepcopy(self.approval)
        approval["payload"] = changed
        with self.assertRaises(ContractError):
            initialise_fleet_brand_intelligence(
                self.config, ROOT, self.database, approval, approved_by="human_owner",
            )


if __name__ == "__main__":
    unittest.main()
