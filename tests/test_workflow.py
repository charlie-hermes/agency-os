from __future__ import annotations

import unittest

from agency_os.contracts import verify_record
from agency_os.store import AuthorizationError, Principal
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

    def test_type_distinct_records_coexist_under_their_own_ids(self) -> None:
        result = run_fictional_article()
        director = Principal("agent_director", "agency-director", "brand_lantern")
        approver = Principal("human_owner", "human-approver", "brand_lantern")
        publisher = Principal(
            "agent_publisher", "publishing-operator", "brand_lantern"
        )
        retrieved = (
            result.store.get(director, "manifest_guide_v1"),
            result.store.get(approver, "approval_guide_v1"),
            result.store.get(publisher, "receipt_idem_guide_v1"),
        )
        self.assertEqual(
            [record["artifact_type"] for record in retrieved],
            ["publication_manifest", "approval_record", "publication_receipt"],
        )

    def test_publisher_can_read_manifest_but_not_private_asset_packages(self) -> None:
        result = run_fictional_article()
        publisher = Principal(
            "agent_publisher", "publishing-operator", "brand_lantern"
        )
        manifest = result.store.get(publisher, "manifest_guide_v1")
        self.assertNotIn("internal_notes", str(manifest))
        for record_id in ("draft_guide_v1", "complete_guide_v1"):
            with self.assertRaises(AuthorizationError):
                result.store.get(publisher, record_id)


if __name__ == "__main__":
    unittest.main()
