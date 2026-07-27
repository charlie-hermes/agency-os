from __future__ import annotations

import json
import unittest
from pathlib import Path


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
            "LearningContextManifest",
            "FailureObservation",
            "CandidateLearning",
            "LearningRecord",
        }
        self.assertTrue(expected.issubset(definitions))

    def test_acceptance_matrix_commands_are_release_blocking(self) -> None:
        matrix = json.loads((ROOT / "acceptance/matrix.json").read_text())
        self.assertEqual(
            matrix["candidate_phase"], "gate-5-foundation-fictional"
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
