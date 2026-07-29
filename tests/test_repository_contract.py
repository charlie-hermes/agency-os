from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]


class RepositoryContractTests(unittest.TestCase):
    def test_master_plan_keeps_end_to_end_gates_visible(self) -> None:
        plan = (ROOT / "docs/master-plan.md").read_text()
        expected_sections = {
            "Gate 4 — authenticated runtime, credential broker and restricted egress",
            "Gate 5 — authoritative platform adapters and tenant data foundation",
            "Gate 6 — governed product-decision workshop",
            "Gate 7 — complete fictional Search Authority Core slice",
            "Gate 8 — optional fictional Social Amplifier",
            "Gate 9 — two-brand isolation proof",
            "Gate 10 — operator and client experience",
            "Gate 11 — staged real integrations",
            "Gate 12 — production operations and final acceptance",
        }
        for section in expected_sections:
            self.assertIn(section, plan)
        self.assertIn("Paperclip remains the only task, approval and audit authority", plan)
        self.assertIn("Product choices remain owner decisions", plan)

    def test_role_catalogue_is_complete(self) -> None:
        catalogue = json.loads((ROOT / "config/roles.json").read_text())
        roles = catalogue["roles"]
        self.assertEqual(len(roles), 12)
        self.assertEqual(len({role["role_id"] for role in roles}), 12)
        for role in roles:
            profile = ROOT / role["profile_path"]
            self.assertTrue((profile / "AGENTS.md").is_file(), role["role_id"])
            self.assertTrue((profile / "SOUL.md").is_file(), role["role_id"])

    def test_required_schema_definitions_exist(self) -> None:
        definitions: set[str] = set()
        for path in (ROOT / "schemas").glob("*.schema.json"):
            schema = json.loads(path.read_text())
            definitions.update(schema.get("$defs", {}))
        expected = {
            "DraftAssetPackage",
            "CompleteAssetPackage",
            "QAPassedAssetPackage",
            "PublicationManifest",
            "ApprovalRecord",
            "PublicationReceipt",
            "CapabilityRecord",
            "ApproverPolicy",
            "PaperclipTask",
            "PaperclipTaskApproval",
            "BuzzContextPacket",
            "BuzzDecisionSummary",
            "EvidenceRecord",
            "WorkQueueItem",
            "LearningContextManifest",
            "FailureObservation",
            "CandidateLearning",
            "LearningRecord",
            "BrandProfile",
            "CampaignBrief",
            "SourceObservation",
            "ResearchPack",
            "ContentPlan",
            "ContentBrief",
            "QAVerdict",
            "ValidationReport",
            "PerformanceSnapshot",
            "OptimisationProposal",
            "RuntimeBundle",
            "OperatorProjection",
            "BrandTenant",
            "PortalHostnameBinding",
            "ProductEntitlement",
            "BrandSource",
            "BrandEntity",
            "BrandClaim",
            "ClaimEvidence",
            "BrandPolicy",
            "BrandCapability",
            "CustomerMission",
            "ObservationRun",
            "Observation",
            "MarketFinding",
            "RemediationProposal",
            "Experiment",
            "OutcomeEvent",
        }
        self.assertTrue(expected.issubset(definitions))

    def test_fleet_generation2_schema_validates_every_record_type_strictly(self) -> None:
        schema = json.loads((ROOT / "schemas/fleet-generation2.schema.json").read_text())
        fixtures = json.loads(
            (ROOT / "fixtures/fleet-generation2-schema-records.json").read_text()
        )["records"]
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        expected_types = {
            "brand_tenant", "portal_hostname_binding", "product_entitlement",
            "brand_source", "brand_entity", "brand_claim", "claim_evidence",
            "brand_policy", "brand_capability", "customer_mission",
            "observation_run", "observation", "market_finding",
            "remediation_proposal", "experiment", "outcome_event",
        }
        self.assertEqual({record["artifact_type"] for record in fixtures}, expected_types)
        for record in fixtures:
            validator.validate(record)
            with self.subTest(record=record["artifact_type"], failure="unknown field"):
                invalid = copy.deepcopy(record)
                invalid["internal_notes"] = "must never pass a public contract"
                self.assertTrue(list(validator.iter_errors(invalid)))
            with self.subTest(record=record["artifact_type"], failure="weak brand id"):
                invalid = copy.deepcopy(record)
                invalid["brand_id"] = "brand_"
                self.assertTrue(list(validator.iter_errors(invalid)))
        self.assertTrue(list(validator.iter_errors({})))
        self.assertTrue(list(validator.iter_errors({"artifact_type": "invented"})))

    def test_paperclip_generation2_template_is_versioned_dependency_only_and_acyclic(self) -> None:
        template = json.loads(
            (ROOT / "config/paperclip-generation2-template.json").read_text()
        )
        self.assertEqual(template["template_version"], "1.0")
        self.assertEqual(template["authority"], "Paperclip")
        self.assertEqual(template["scheduling"], "dependency-and-evidence-only")
        self.assertNotIn("week", json.dumps(template).lower())
        issues = {issue["key"]: issue for issue in template["issues"]}
        self.assertEqual(set(issues), {"FL2-00", "FL2-10", "FL2-20", "FL2-30", "FL2-40", "FL2-50", "FL2-60", "FL2-70", "FL2-80", "FL2-90", "FL2-100"})
        self.assertEqual(issues["FL2-00"]["parent"], None)
        for key, issue in issues.items():
            for dependency in issue["depends_on"]:
                self.assertIn(dependency, issues)
                self.assertNotEqual(dependency, key)
        visited: set[str] = set()
        visiting: set[str] = set()
        def visit(key: str) -> None:
            if key in visiting:
                self.fail("Paperclip template contains a dependency cycle")
            if key in visited:
                return
            visiting.add(key)
            for dependency in issues[key]["depends_on"]:
                visit(dependency)
            visiting.remove(key)
            visited.add(key)
        for key in issues:
            visit(key)

    def test_fleet_generation2_manifest_is_bound_to_live_internal_company(self) -> None:
        config = json.loads((ROOT / "config/fleet-generation2.json").read_text())
        self.assertEqual(config["business_name"], "Fleet")
        self.assertEqual(config["base_domain"], "madebyfleet.com")
        self.assertEqual(config["internal_pilot"]["brand_id"], "brand_fleet")
        self.assertEqual(config["internal_pilot"]["paperclip_company_id"], "d7e2e389-c7ad-486e-87ca-482e4ec6216d")
        self.assertEqual(config["internal_pilot"]["paperclip_company_name"], "Fleet DMA")
        self.assertEqual(
            {item["module"] for item in config["product_entitlements"]},
            {
                "content_engine",
                "brand_twin",
                "ai_market_observatory",
                "brand_agent",
                "controlled_actions",
            },
        )
        self.assertTrue(config["disabled_by_default"])
        entitlement = config["product_entitlements"][0]
        self.assertEqual(entitlement["version"], 1)
        self.assertEqual(entitlement["effective_at"], "1970-01-01T00:00:00Z")
        self.assertIsNone(entitlement["expires_at"])
        self.assertIsNone(entitlement["supersedes_entitlement_id"])

    def test_acceptance_matrix_commands_are_release_blocking(self) -> None:
        matrix = json.loads((ROOT / "acceptance/matrix.json").read_text())
        self.assertEqual(
            matrix["candidate_phase"], "gate-7-core-fictional"
        )
        self.assertGreaterEqual(len(matrix["criteria"]), 10)
        for criterion in matrix["criteria"]:
            self.assertTrue(criterion["release_blocker"])
            self.assertTrue(criterion["command"].startswith("python3 -m unittest "))

    def test_linux_verification_contract_is_explicit_and_ci_enforced(self) -> None:
        readme = (ROOT / "README.md").read_text()
        verifier = (ROOT / "scripts/verify").read_text()
        workflow = (ROOT / ".github/workflows/verify.yml").read_text()
        self.assertIn("supported on **Linux only**", readme)
        self.assertIn("SO_PEERCRED", readme)
        self.assertIn('"$(uname -s)" != "Linux"', verifier)
        self.assertIn("/proc/$$/exe", verifier)
        self.assertIn('hasattr(socket, "SO_PEERCRED")', verifier)
        self.assertIn("runs-on: ubuntu-latest", workflow)
        self.assertIn("run: ./scripts/verify", workflow)


if __name__ == "__main__":
    unittest.main()
