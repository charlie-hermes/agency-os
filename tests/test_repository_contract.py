from __future__ import annotations

import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RepositoryContractTests(unittest.TestCase):
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
            "LearningContextManifest",
            "FailureObservation",
            "CandidateLearning",
            "LearningRecord",
        }
        self.assertTrue(expected.issubset(definitions))

    def test_acceptance_matrix_commands_are_release_blocking(self) -> None:
        matrix = json.loads((ROOT / "acceptance/matrix.json").read_text())
        self.assertEqual(matrix["candidate_phase"], "0/1-fictional")
        self.assertGreaterEqual(len(matrix["criteria"]), 10)
        for criterion in matrix["criteria"]:
            self.assertTrue(criterion["release_blocker"])
            self.assertTrue(criterion["command"].startswith("python3 -m unittest "))


if __name__ == "__main__":
    unittest.main()
