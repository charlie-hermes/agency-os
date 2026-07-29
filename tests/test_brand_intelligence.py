from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from agency_os.brand_intelligence import (
    ApprovalBinding,
    BrandIntelligenceAuthority,
    BrandIntelligenceAuthorizationError,
    make_generation2_record,
    source_reference,
)
from agency_os.contracts import ContractError
from agency_os.store import Principal


NOW = "2026-07-29T00:00:00Z"
MATERIAL = {
    "source_id": "material_fleet_plan",
    "source_version": 1,
    "source_checksum": "sha256:" + "1" * 64,
    "locator": "repo://docs/fleet-unified-platform-enterprise-plan.md",
}


class BrandIntelligenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.database = self.root / "brand-intelligence.sqlite3"
        self.authority = BrandIntelligenceAuthority(self.database)
        self.director = Principal("director_fleet", "agency-director", "brand_fleet")
        self.steward = Principal("steward_fleet", "brand-brief-steward", "brand_fleet")
        self.analyst = Principal("analyst_fleet", "growth-intelligence-analyst", "brand_fleet")
        self.reviewer = Principal("reviewer_fleet", "platform-assurance-reviewer", "brand_fleet")
        self.foreign = Principal("director_other", "agency-director", "brand_other")
        self.approval = ApprovalBinding(
            "10000000-0000-4000-8000-000000000155", "sha256:" + "2" * 64,
            "human_owner", NOW,
        )
        self.source = self._source()
        self.authority.put(self.director, self.source)
        self.source_ref = source_reference(self.source)
        self.entity = self._record(
            "brand_entity", self.director, source_refs=[self.source_ref],
            entity_id="entity_fleet", entity_type="brand", canonical_name="Fleet",
            aliases=["Made by Fleet"], status="active", evidence_refs=[self.source_ref],
        )
        self.authority.put(self.director, self.entity)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def _record(
        self,
        artifact_type: str,
        principal: Principal,
        *,
        source_refs: list[dict[str, object]] | None = None,
        **fields: object,
    ) -> dict[str, object]:
        return make_generation2_record(
            artifact_type,
            brand_id=principal.brand_id,
            created_by=principal.actor_id,
            provenance=source_refs or [self.source_ref],
            created_at=NOW,
            effective_at=NOW,
            **fields,
        )

    def _source(self, *, expires_at: str | None = None) -> dict[str, object]:
        return make_generation2_record(
            "brand_source", brand_id="brand_fleet", created_by="director_fleet",
            provenance=[MATERIAL], created_at=NOW, effective_at=NOW,
            source_id="source_fleet_plan", source_class="first_party",
            authority="authoritative", locator=MATERIAL["locator"], version=1,
            status="active", owner_id="director_fleet", observed_at=NOW,
            expires_at=expires_at,
        )

    def _claim(
        self,
        claim_id: str = "claim_fleet_operates_content",
        *,
        value: object = "governed automated content production",
        approval_state: str = "approved",
        status: str = "active",
    ) -> dict[str, object]:
        return self._record(
            "brand_claim", self.director, claim_id=claim_id,
            subject_entity_id="entity_fleet", predicate="provides", object=value,
            status=status, approval_state=approval_state, owner_id="director_fleet",
            evidence_refs=[self.source_ref], valid_from=NOW, valid_until=None,
        )

    def _evidence(self, claim_id: str, suffix: str = "1") -> dict[str, object]:
        return self._record(
            "claim_evidence", self.director,
            evidence_id=f"evidence_{claim_id}_{suffix}", claim_id=claim_id,
            source_ref=self.source_ref, extract="The approved plan states this capability.",
            confidence=1.0, observed_at=NOW, status="active",
        )

    def _mission(self, mission_id: str = "mission_content_001") -> dict[str, object]:
        return self._record(
            "customer_mission", self.analyst, mission_id=mission_id,
            audience="brand leader", intent="find governed content automation",
            success_definition={"launch_priority": True, "evidence": "repeatable"},
            variants=["How can a brand automate content safely?", "Safe AI content automation"],
            status="active",
        )

    def test_approved_claim_requires_paperclip_binding_and_evidence(self) -> None:
        claim = self._claim()
        with self.assertRaisesRegex(ContractError, "Paperclip approval"):
            self.authority.put(self.director, claim)
        self.authority.put(self.director, claim, approval=self.approval)
        profile = self.authority.operating_profile(self.reviewer, at="2026-07-30T12:00:00Z")
        self.assertEqual(profile["claims"], [])
        self.assertEqual(profile["evidence_gaps"][0]["reason"], "evidence_missing_or_stale")
        self.authority.put(self.director, self._evidence(claim["claim_id"]))
        profile = self.authority.operating_profile(self.reviewer, at="2026-07-30T12:00:00Z")
        self.assertEqual([item["claim_id"] for item in profile["claims"]], [claim["claim_id"]])
        self.assertEqual(profile["conflicts"], [])

    def test_draft_inactive_and_expired_claims_cannot_ground_content(self) -> None:
        draft = self._claim("claim_draft", approval_state="draft")
        self.authority.put(self.director, draft)
        inactive = self._claim("claim_inactive", status="inactive")
        self.authority.put(self.director, inactive, approval=self.approval)
        self.authority.put(self.director, self._evidence("claim_inactive"))
        profile = self.authority.operating_profile(self.reviewer, at="2026-07-30T12:00:00Z")
        reasons = {item["claim_id"]: item["reason"] for item in profile["evidence_gaps"]}
        self.assertEqual(reasons["claim_draft"], "not_approved")
        self.assertEqual(reasons["claim_inactive"], "inactive")
        with self.assertRaises(BrandIntelligenceAuthorizationError):
            self.authority.content_grounding(self.reviewer, required_claim_ids=["claim_draft"])

    def test_conflicting_approved_claims_are_visible_and_excluded(self) -> None:
        first = self._claim("claim_service_a", value="governed content")
        second = self._claim("claim_service_b", value="unrestricted content")
        for claim in (first, second):
            self.authority.put(self.director, claim, approval=self.approval)
            self.authority.put(self.director, self._evidence(claim["claim_id"]))
        profile = self.authority.operating_profile(self.reviewer, at="2026-07-30T12:00:00Z")
        self.assertEqual(profile["claims"], [])
        self.assertEqual(profile["conflicts"][0]["claim_ids"], ["claim_service_a", "claim_service_b"])

    def test_strict_fields_checksum_role_actor_and_tenant_fail_closed(self) -> None:
        claim = self._claim()
        unknown = copy.deepcopy(claim)
        unknown["internal_notes"] = "not admitted"
        from agency_os.contracts import finalize_record
        unknown = finalize_record(unknown)
        with self.assertRaisesRegex(ContractError, "strict fields"):
            self.authority.put(self.director, unknown, approval=self.approval)
        changed = copy.deepcopy(claim)
        changed["object"] = "tampered"
        with self.assertRaisesRegex(ContractError, "checksum"):
            self.authority.put(self.director, changed, approval=self.approval)
        with self.assertRaises(BrandIntelligenceAuthorizationError):
            self.authority.put(self.analyst, claim, approval=self.approval)
        with self.assertRaises(BrandIntelligenceAuthorizationError):
            self.authority.put(self.foreign, claim, approval=self.approval)
        with self.assertRaises(KeyError):
            self.authority.get(self.foreign, "brand_source", self.source["source_id"])

    def test_immutable_versions_and_restart_preserve_approval(self) -> None:
        claim = self._claim()
        self.authority.put(self.director, claim, approval=self.approval)
        self.authority.put(self.director, claim, approval=self.approval)
        changed = copy.deepcopy(claim)
        changed["object"] = "changed without version"
        from agency_os.contracts import finalize_record
        changed = finalize_record(changed)
        with self.assertRaises(ContractError):
            self.authority.put(self.director, changed, approval=self.approval)
        self.authority.put(self.director, self._evidence(claim["claim_id"]))
        restarted = BrandIntelligenceAuthority(self.database)
        grounding = restarted.content_grounding(self.reviewer, required_claim_ids=[claim["claim_id"]])
        self.assertEqual(grounding["claim_ids"], [claim["claim_id"]])
        self.assertEqual(self.database.stat().st_mode & 0o777, 0o600)
        self.assertEqual(restarted.schema_version(), 1)

    def test_finding_requires_two_complete_runs_for_every_mission(self) -> None:
        mission = self._mission()
        self.authority.put(self.analyst, mission)
        run_ids = ["run_baseline_a", "run_baseline_b"]
        observations = []
        for index, run_id in enumerate(run_ids):
            run = self._record(
                "observation_run", self.analyst, run_id=run_id,
                mission_ids=[mission["mission_id"]], adapter="approved_manual_search",
                adapter_version="1", model="public-search", model_version="snapshot-1",
                started_at=NOW, completed_at=NOW, status="complete",
            )
            self.authority.put(self.analyst, run)
            observation = self._record(
                "observation", self.analyst, observation_id=f"observation_{index}",
                run_id=run_id, mission_id=mission["mission_id"],
                variant=mission["variants"][index], response="No verified Fleet result.",
                citations=[], evaluations={"fleet_mentioned": False, "external_ai": "unknown"},
                observed_at=NOW, status="active",
            )
            self.authority.put(self.analyst, observation)
            observations.append(observation)
        finding = self._record(
            "market_finding", self.analyst, finding_id="finding_public_page_gap",
            mission_ids=[mission["mission_id"]],
            observation_refs=[observations[0]["observation_id"]],
            finding="Fleet has no verified public explainer in the approved baseline.",
            classification="content_gap", confidence=0.9,
            knowns=["The approved query returned no exact Fleet domain result."],
            inferences=["A public explainer may improve retrievability."],
            unknowns=["External AI assistant coverage is not measured."],
            paperclip_issue_id="PAP-158", status="active",
        )
        with self.assertRaisesRegex(ContractError, "two runs"):
            self.authority.admit_finding(self.analyst, finding)
        finding["observation_refs"].append(observations[1]["observation_id"])
        from agency_os.contracts import finalize_record
        finding = finalize_record(finding)
        self.authority.admit_finding(self.analyst, finding)
        summary = self.authority.observatory_summary(self.reviewer)
        self.assertEqual(summary["complete_run_count"], 2)
        self.assertEqual(summary["observation_count"], 2)
        self.assertEqual(summary["finding_count"], 1)
        self.assertEqual(summary["external_ai_coverage"], "unknown")

    def test_audit_is_brand_scoped_and_denials_are_recorded(self) -> None:
        with self.assertRaises(BrandIntelligenceAuthorizationError):
            self.authority.put(self.analyst, self._claim(), approval=self.approval)
        events = self.authority.audit_events(self.reviewer)
        self.assertTrue(any(item["outcome"] == "DENY_ROLE" for item in events))
        foreign_root = self.root / "foreign"
        foreign_root.mkdir(mode=0o700)
        foreign_authority = BrandIntelligenceAuthority(foreign_root / "authority.sqlite3")
        foreign_reviewer = Principal("reviewer_other", "platform-assurance-reviewer", "brand_other")
        self.assertEqual(foreign_authority.audit_events(foreign_reviewer), [])


if __name__ == "__main__":
    unittest.main()
