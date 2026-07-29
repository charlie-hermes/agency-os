from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agency_os.brand_agent_host import BrandAgentHostError, load_runtime_config
from agency_os.contracts import canonical_checksum
from scripts.prepare_fleet_brand_agent import activation_manifest


ROOT = Path(__file__).resolve().parents[1]
COMMIT = "1" * 40


class BrandAgentReleaseTests(unittest.TestCase):
    def test_activation_manifest_is_exact_checksums_and_no_external_write(self) -> None:
        first = activation_manifest(ROOT, commit=COMMIT)
        second = activation_manifest(ROOT, commit=COMMIT)
        self.assertEqual(first, second)
        self.assertEqual(first["brand_id"], "brand_fleet")
        self.assertEqual(first["paperclip_company_id"], "d7e2e389-c7ad-486e-87ca-482e4ec6216d")
        self.assertEqual(first["controlled_action"], "request_human_follow_up")
        self.assertTrue(first["action_confirmation_required"])
        self.assertTrue(first["action_reversible"])
        self.assertFalse(first["external_model"])
        self.assertFalse(first["provider_external_writes"])
        self.assertEqual(len(first["public_claim_ids"]), 7)
        self.assertEqual(
            [item["name"] for item in first["release_artifacts"]],
            ["brand_agent_policy", "brand_agent_runtime_config", "brand_agent_acceptance_matrix"],
        )
        for artifact in first["release_artifacts"]:
            self.assertRegex(artifact["checksum"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(canonical_checksum(first), r"^sha256:[0-9a-f]{64}$")

    def test_runtime_config_is_secret_free_absolute_and_loopback_only(self) -> None:
        config = load_runtime_config(ROOT / "config/fleet-brand-agent-runtime.json")
        self.assertEqual(config["brand_id"], "brand_fleet")
        self.assertEqual(config["allowed_hosts"], ["127.0.0.1", "localhost"])
        self.assertTrue(all(item.startswith("http://127.0.0.1") or item.startswith("http://localhost") for item in config["allowed_origins"]))
        encoded = json.dumps(config).lower()
        self.assertNotIn("private-test-key", encoded)
        self.assertNotIn("bearer ", encoded)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "bad.json"
            changed = dict(config)
            changed["unknown"] = True
            path.write_text(json.dumps(changed), encoding="utf-8")
            with self.assertRaises(BrandAgentHostError):
                load_runtime_config(path)

    def test_acceptance_matrix_is_unique_release_blocking_and_executable(self) -> None:
        matrix = json.loads(
            (ROOT / "acceptance/fleet-brand-agent.json").read_text(encoding="utf-8")
        )
        self.assertEqual(matrix["candidate_gate"], "G2.5")
        self.assertEqual(matrix["paperclip_issue"], "PAP-159")
        identifiers = [item["id"] for item in matrix["criteria"]]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertGreaterEqual(len(identifiers), 12)
        for criterion in matrix["criteria"]:
            self.assertTrue(criterion["release_blocker"])
            self.assertTrue(criterion["command"])
            self.assertTrue(criterion["expected_evidence"])
            self.assertNotRegex(criterion["expected_evidence"], r"(?i)weekly|week [0-9]")

    def test_entitlements_preserve_g2_5_and_add_only_the_approved_portal(self) -> None:
        config = json.loads(
            (ROOT / "config/fleet-generation2.json").read_text(encoding="utf-8")
        )
        enabled = {item["module"]: item for item in config["product_entitlements"]}
        self.assertEqual(
            set(enabled),
            {
                "content_engine", "brand_twin", "ai_market_observatory",
                "brand_agent", "controlled_actions", "client_portal",
            },
        )
        self.assertEqual(
            enabled["controlled_actions"]["limits"],
            {
                "action": "request_human_follow_up",
                "external_write": False,
                "confirmation_required": True,
            },
        )
        self.assertEqual(enabled["client_portal"]["limits"]["production_tenants"], 1)
        self.assertEqual(enabled["client_portal"]["limits"]["production_brand"], "brand_fleet")
        self.assertNotIn("measurement", enabled)
        self.assertNotIn("agentic_commerce", enabled)


if __name__ == "__main__":
    unittest.main()
