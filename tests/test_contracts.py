from __future__ import annotations

import copy
import unittest

from agency_os.contracts import (
    ContractError,
    canonical_checksum,
    finalize_record,
    make_approval_record,
    make_publication_manifest,
    verify_record,
)
from agency_os.workflow import run_fictional_article


class ContractTests(unittest.TestCase):
    def test_canonical_checksum_ignores_object_insertion_order(self) -> None:
        left = {"z": 1, "a": {"two": 2, "one": 1}}
        right = {"a": {"one": 1, "two": 2}, "z": 1}
        self.assertEqual(canonical_checksum(left), canonical_checksum(right))

    def test_tampering_invalidates_record(self) -> None:
        record = finalize_record({"artifact_type": "test", "brand_id": "brand_a"})
        record["brand_id"] = "brand_b"
        with self.assertRaises(ContractError):
            verify_record(record)

    def test_approval_rejects_malformed_paperclip_evidence_binding(self) -> None:
        result = run_fictional_article()
        manifest = result.records["manifest"]
        with self.assertRaises(ContractError):
            make_approval_record(
                approval_id="approval_invalid_evidence",
                manifest=manifest,
                approver_id="human_owner",
                authority_role="brand_owner",
                decided_at=result.records["approval"]["decided_at"],
                expires_at=result.records["approval"]["expires_at"],
                paperclip_approval_id="not-a-uuid",
                paperclip_approval_evidence_checksum="not-a-checksum",
            )

    def test_manifest_excludes_internal_notes(self) -> None:
        result = run_fictional_article()
        qa_package = result.records["qa_package"]
        manifest = result.records["manifest"]
        self.assertNotIn("internal_notes", manifest)
        self.assertNotIn(
            qa_package["payload"]["internal_notes"][0], str(manifest["public_fields"])
        )

    def test_changed_qa_package_requires_a_new_manifest(self) -> None:
        result = run_fictional_article()
        qa_package = copy.deepcopy(result.records["qa_package"])
        old_manifest = result.records["manifest"]
        qa_package["payload"]["public_fields"]["title"] = "Changed"
        qa_package = finalize_record(qa_package)
        new_manifest = make_publication_manifest(
            manifest_id="manifest_changed",
            qa_package=qa_package,
            destination_ref=old_manifest["destination_ref"],
            environment=old_manifest["environment"],
            operation=old_manifest["operation"],
            schedule_window=old_manifest["schedule_window"],
            transformation_version=old_manifest["transformation_version"],
        )
        self.assertNotEqual(
            new_manifest["content_checksum"], old_manifest["content_checksum"]
        )


if __name__ == "__main__":
    unittest.main()
