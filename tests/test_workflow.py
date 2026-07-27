from __future__ import annotations

import unittest

from agency_os.contracts import verify_record
from agency_os.store import Principal
from agency_os.workflow import run_fictional_article


class WorkflowTests(unittest.TestCase):
    def test_complete_fictional_flow(self) -> None:
        result = run_fictional_article()
        expected = {
            "brand",
            "brief",
            "draft",
            "complete",
            "qa_verdict",
            "qa_package",
            "manifest",
            "approval",
            "receipt",
            "performance",
            "learning_context",
            "candidate_learning",
            "learning_record",
        }
        self.assertEqual(set(result.records), expected)
        for record in result.records.values():
            verify_record(record)
        self.assertEqual(result.records["receipt"]["state"], "PUBLISHED")
        self.assertEqual(result.publisher.calls, 1)
        self.assertTrue(result.records["receipt"]["validation"]["matches_manifest"])
        self.assertEqual(
            result.records["performance"]["conclusion_class"],
            "insufficient_evidence",
        )
        director = Principal(
            "agent_director", "agency-director", "brand_lantern"
        )
        active = result.store.active_learning(director)
        self.assertEqual(
            [record["learning_record_id"] for record in active],
            ["learning_guide_v1"],
        )

    def test_vertical_slice_publishes_only_public_fields(self) -> None:
        result = run_fictional_article()
        internal_canary = result.records["draft"]["payload"]["internal_notes"][0]
        published = result.publisher.objects["idem_guide_v1"][
            "rendered_public_fields"
        ]
        self.assertNotIn(internal_canary, str(published))
        self.assertEqual(published, result.records["manifest"]["public_fields"])


if __name__ == "__main__":
    unittest.main()
