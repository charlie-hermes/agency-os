from __future__ import annotations

import unittest

from agency_os.contracts import verify_record
from agency_os.core_workflow import run_core_workflow
from agency_os.fictional_platforms import (
    InMemoryBuzzTransport,
    InMemoryPaperclipBoardTransport,
    InMemoryPaperclipTransport,
)
from agency_os.gateway import MockPublisher
from agency_os.integrations import (
    PaperclipBoardApprovalAdapter,
    PaperclipBrandBinding,
    PaperclipLifecycleAdapter,
    TypedBuzzAdapter,
)
from agency_os.social_workflow import (
    SOCIAL_BRANCH_ROLES,
    SocialAmplifierDenied,
    run_social_workflow,
)


def _dependencies(company_id: str, brand_id: str):
    binding = PaperclipBrandBinding(company_id, brand_id)
    transport = InMemoryPaperclipTransport(
        company_id=binding.company_id,
        brand_id=binding.brand_id,
    )
    lifecycle = PaperclipLifecycleAdapter(transport, binding)
    board = PaperclipBoardApprovalAdapter(
        InMemoryPaperclipBoardTransport(transport),
        binding,
    )
    buzz = TypedBuzzAdapter(InMemoryBuzzTransport(), binding.brand_id)
    return transport, lifecycle, board, buzz


def _approve(board):
    def authority(requested, _manifest):
        return board.decide_approval(
            requested["id"],
            decision="approve",
            decision_note="Exact fictional manifest approved for acceptance testing.",
        )

    return authority


class SocialWorkflowTests(unittest.TestCase):
    def test_social_branch_is_absent_when_product_is_core_only(self) -> None:
        transport, lifecycle, board, buzz = _dependencies(
            "00000000-0000-4000-8000-000000000001",
            "brand_lantern",
        )
        core = run_core_workflow(
            paperclip=lifecycle,
            buzz=buzz,
            approval_authority=_approve(board),
            publisher=MockPublisher(),
        )
        issue_count = len(transport.issues)
        social_publisher = MockPublisher()
        with self.assertRaises(SocialAmplifierDenied):
            run_social_workflow(
                core=core,
                paperclip=lifecycle,
                buzz=buzz,
                approval_authority=_approve(board),
                publisher=social_publisher,
            )
        self.assertEqual(len(transport.issues), issue_count)
        self.assertEqual(social_publisher.calls, 0)

    def test_social_branch_requires_completed_canonical_approval(self) -> None:
        transport, lifecycle, board, buzz = _dependencies(
            "00000000-0000-4000-8000-000000000002",
            "brand_orchard",
        )
        core = run_core_workflow(
            paperclip=lifecycle,
            buzz=buzz,
            approval_authority=_approve(board),
            publisher=MockPublisher(),
            brand_name="Orchard Window Co.",
            product_tier="search_authority_social",
        )
        core.approval["status"] = "pending"
        issue_count = len(transport.issues)
        with self.assertRaises(SocialAmplifierDenied):
            run_social_workflow(
                core=core,
                paperclip=lifecycle,
                buzz=buzz,
                approval_authority=_approve(board),
                publisher=MockPublisher(),
            )
        self.assertEqual(len(transport.issues), issue_count)

    def test_complete_social_branch_is_checksum_bound_and_closed(self) -> None:
        _, lifecycle, board, buzz = _dependencies(
            "00000000-0000-4000-8000-000000000003",
            "brand_meadow",
        )
        core = run_core_workflow(
            paperclip=lifecycle,
            buzz=buzz,
            approval_authority=_approve(board),
            publisher=MockPublisher(),
            brand_name="Meadow Balcony Co.",
            product_tier="search_authority_social",
        )
        result = run_social_workflow(
            core=core,
            paperclip=lifecycle,
            buzz=buzz,
            approval_authority=_approve(board),
            publisher=MockPublisher(),
        )
        self.assertEqual(tuple(result.tasks_by_role), SOCIAL_BRANCH_ROLES)
        self.assertEqual(
            {task["status"] for task in result.tasks_by_role.values()},
            {"done"},
        )
        self.assertEqual(result.records["social_qa"]["payload"]["verdict"], "PASS")
        self.assertEqual(
            result.records["social_qa"]["payload"]["reviewed_checksum"],
            result.records["social_package"]["content_checksum"],
        )
        self.assertEqual(result.approval["status"], "approved")
        self.assertEqual(
            result.approval["payload"]["content_checksum"],
            result.records["published_manifest"]["content_checksum"],
        )
        self.assertEqual(result.records["published_receipt"]["state"], "PUBLISHED")
        self.assertEqual(result.vertical_slice.publisher.calls, 1)
        self.assertFalse(result.external_writes)
        for record in result.records.values():
            verify_record(record)

    def test_two_brands_are_independent_and_cross_brand_branch_is_denied(self) -> None:
        first = _dependencies(
            "00000000-0000-4000-8000-000000000004",
            "brand_lantern",
        )
        second = _dependencies(
            "00000000-0000-4000-8000-000000000005",
            "brand_orchard",
        )
        _, lifecycle_a, board_a, buzz_a = first
        transport_b, lifecycle_b, board_b, buzz_b = second
        core_a = run_core_workflow(
            paperclip=lifecycle_a,
            buzz=buzz_a,
            approval_authority=_approve(board_a),
            publisher=MockPublisher(),
            campaign_id="camp_lantern",
            brand_name="Lantern Garden Co.",
            product_tier="search_authority_social",
        )
        core_b = run_core_workflow(
            paperclip=lifecycle_b,
            buzz=buzz_b,
            approval_authority=_approve(board_b),
            publisher=MockPublisher(),
            campaign_id="camp_orchard",
            brand_name="Orchard Window Co.",
            product_tier="search_authority_social",
        )
        self.assertEqual(core_a.records["published_manifest"]["brand_id"], "brand_lantern")
        self.assertEqual(core_b.records["published_manifest"]["brand_id"], "brand_orchard")
        issue_count_b = len(transport_b.issues)
        with self.assertRaises(SocialAmplifierDenied):
            run_social_workflow(
                core=core_a,
                paperclip=lifecycle_b,
                buzz=buzz_b,
                approval_authority=_approve(board_b),
                publisher=MockPublisher(),
            )
        self.assertEqual(len(transport_b.issues), issue_count_b)
        social_b = run_social_workflow(
            core=core_b,
            paperclip=lifecycle_b,
            buzz=buzz_b,
            approval_authority=_approve(board_b),
            publisher=MockPublisher(),
            campaign_id="camp_orchard_social",
        )
        self.assertEqual(
            social_b.records["published_manifest"]["brand_id"],
            "brand_orchard",
        )


if __name__ == "__main__":
    unittest.main()
