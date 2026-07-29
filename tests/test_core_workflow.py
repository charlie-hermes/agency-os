from __future__ import annotations

import json
import subprocess
import sys
import unittest

from agency_os.contracts import verify_record
from agency_os.core_demo import run_fictional_core_workflow
from agency_os.core_workflow import CORE_RUNTIME_ROLES, CoreApprovalDenied, run_core_workflow
from agency_os.fictional_platforms import (
    InMemoryBuzzTransport,
    InMemoryPaperclipBoardTransport,
    InMemoryPaperclipTransport,
)
from agency_os.gateway import MockPublisher
from agency_os.integrations import (
    IntegrationError,
    PaperclipBoardApprovalAdapter,
    PaperclipBrandBinding,
    PaperclipLifecycleAdapter,
    TypedBuzzAdapter,
)

from agency_os.operator_view import build_campaign_projection

def _dependencies():
    binding = PaperclipBrandBinding(
        "00000000-0000-4000-8000-000000000001", "brand_lantern"
    )
    transport = InMemoryPaperclipTransport(
        company_id=binding.company_id, brand_id=binding.brand_id
    )
    lifecycle = PaperclipLifecycleAdapter(transport, binding)
    board = PaperclipBoardApprovalAdapter(InMemoryPaperclipBoardTransport(transport), binding)
    buzz = TypedBuzzAdapter(InMemoryBuzzTransport(), binding.brand_id)
    return transport, lifecycle, board, buzz, MockPublisher()


class CoreWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = run_fictional_core_workflow()

    def test_complete_core_flow_has_real_revision_and_authoritative_closure(self) -> None:
        result = self.result
        self.assertEqual(tuple(result.tasks_by_role), CORE_RUNTIME_ROLES)
        self.assertEqual({task["status"] for task in result.tasks_by_role.values()}, {"done"})
        director_id = result.tasks_by_role["agency-director"]["id"]
        child_tasks = [result.tasks_by_role[role] for role in CORE_RUNTIME_ROLES[1:]]
        self.assertTrue(all(task["parentId"] == director_id for task in child_tasks))
        self.assertEqual(child_tasks[0]["blockedByIssueIds"], [])
        self.assertEqual(
            [task["blockedByIssueIds"] for task in child_tasks[1:]],
            [[prior["id"]] for prior in child_tasks[:-1]],
        )
        self.assertEqual(result.records["qa_revise"]["payload"]["verdict"], "REVISE")
        self.assertEqual(result.records["published_qa_verdict"]["payload"]["verdict"], "PASS")
        self.assertNotEqual(result.records["rejected_draft"]["content_checksum"], result.records["published_draft"]["content_checksum"])
        for record in result.records.values():
            verify_record(record)
        self.assertEqual(result.approval["status"], "approved")
        self.assertEqual(result.approval["payload"]["content_checksum"], result.records["published_manifest"]["content_checksum"])
        self.assertEqual(result.records["published_receipt"]["state"], "PUBLISHED")
        evidence = result.records["paperclip_approval_evidence"]
        receipt = result.records["published_receipt"]
        local_approval = result.records["published_approval"]
        self.assertEqual(receipt["paperclip_approval_id"], result.approval["id"])
        self.assertEqual(
            receipt["paperclip_approval_evidence_checksum"],
            evidence["content_checksum"],
        )
        self.assertEqual(local_approval["paperclip_approval_id"], result.approval["id"])
        self.assertEqual(result.vertical_slice.publisher.calls, 1)
        self.assertFalse(result.external_writes)
        self.assertFalse(hasattr(result, "paperclip"))
        self.assertFalse(hasattr(result, "paperclip_transport"))
        self.assertFalse(hasattr(result, "buzz_transport"))

    def test_buzz_decision_is_written_back_and_never_becomes_task_authority(self) -> None:
        result = self.result
        producer_id = result.tasks_by_role["content-producer"]["id"]
        writeback = result.records["buzz_decision_writeback"]
        self.assertEqual(writeback["paperclip_issue_id"], producer_id)
        self.assertEqual(
            writeback["payload"]["decision_authority"],
            "paperclip_writeback",
        )
        self.assertTrue(writeback["payload"]["buzz_message_id"])
        self.assertTrue(writeback["payload"]["paperclip_comment_id"])
        self.assertEqual(result.tasks_by_role["content-producer"]["status"], "done")

    def test_unassigned_revision_stays_in_paperclip_queue_until_closure(self) -> None:
        transport, lifecycle, board, buzz, publisher = _dependencies()

        def approve(requested, _manifest):
            return board.decide_approval(
                requested["id"],
                decision="approve",
                decision_note="Human approved the exact fictional package.",
            )

        run_core_workflow(
            paperclip=lifecycle,
            buzz=buzz,
            approval_authority=approve,
            publisher=publisher,
        )
        revision_updates = [
            payload
            for method, _path, payload in transport.calls
            if method == "PATCH"
            and payload is not None
            and str(payload.get("comment", "")).startswith("QA REVISE binds ")
        ]
        self.assertEqual(len(revision_updates), 1)
        self.assertEqual(revision_updates[0]["status"], "todo")

    def test_separate_campaigns_do_not_reuse_paperclip_tasks(self) -> None:
        transport, lifecycle, board, buzz, publisher = _dependencies()

        def approve(requested, _manifest):
            return board.decide_approval(
                requested["id"],
                decision="approve",
                decision_note="Human approved the exact fictional package.",
            )

        first = run_core_workflow(
            paperclip=lifecycle,
            buzz=buzz,
            approval_authority=approve,
            publisher=publisher,
            campaign_id="camp_one",
            asset_id="asset_one",
        )
        second = run_core_workflow(
            paperclip=lifecycle,
            buzz=buzz,
            approval_authority=approve,
            publisher=publisher,
            campaign_id="camp_two",
            asset_id="asset_two",
        )
        first_ids = {task["id"] for task in first.tasks_by_role.values()}
        second_ids = {task["id"] for task in second.tasks_by_role.values()}
        self.assertTrue(first_ids.isdisjoint(second_ids))
        self.assertEqual(len(transport.issues), 16)
        self.assertTrue(
            all("[camp_one]" in task["title"] for task in first.tasks_by_role.values())
        )
        self.assertTrue(
            all("[camp_two]" in task["title"] for task in second.tasks_by_role.values())
        )

    def test_operator_projection_is_read_only_and_paperclip_derived(self) -> None:
        projection = self.result.operator_projection
        self.assertEqual(projection["authority"], "paperclip")
        self.assertEqual(projection["projection"], "read_only")
        self.assertEqual(projection["task_counts"], {"done": 8})
        self.assertEqual(len(projection["tasks"]), 8)
        self.assertEqual(projection["approvals"][0]["status"], "approved")
        self.assertEqual(projection["campaign_id"], "camp_summer")

        _, lifecycle, _, _, _ = _dependencies()
        included = lifecycle.create_task(
            title="Campaign task",
            campaign_id="camp_summer",
            stage="research",
            acceptance_criteria=["projected"],
            idempotency_key="campaign-task",
            status="todo",
        )
        unrelated = lifecycle.create_task(
            title="Other campaign task",
            campaign_id="camp_other",
            stage="unrelated",
            acceptance_criteria=["not projected"],
            idempotency_key="unrelated-task",
            status="todo",
        )
        scoped = build_campaign_projection(
            lifecycle,
            campaign_id="camp_summer",
            task_ids=[included["id"]],
        )
        self.assertEqual(
            [item["paperclip_issue_id"] for item in scoped["tasks"]],
            [included["id"]],
        )
        with self.assertRaises(IntegrationError):
            build_campaign_projection(
                lifecycle,
                campaign_id="camp_summer",
                task_ids=[included["id"], unrelated["id"]],
            )

    def test_denied_pending_and_altered_approvals_make_zero_publisher_calls(self) -> None:
        for outcome in ("rejected", "pending", "altered"):
            with self.subTest(outcome=outcome):
                transport, lifecycle, board, buzz, publisher = _dependencies()
                observed_calls: list[int] = []

                def authority(requested, manifest):
                    observed_calls.append(publisher.calls)
                    if outcome == "rejected":
                        return board.decide_approval(
                            requested["id"],
                            decision="reject",
                            decision_note="Board rejected the manifest",
                        )
                    if outcome == "altered":
                        decided = board.decide_approval(
                            requested["id"],
                            decision="approve",
                            decision_note="Board response will be tampered",
                        )
                        transport.approvals[requested["id"]]["payload"]["manifest_id"] = "tampered"
                        return decided
                    return requested

                with self.assertRaises(CoreApprovalDenied):
                    run_core_workflow(
                        paperclip=lifecycle,
                        buzz=buzz,
                        approval_authority=authority,
                        publisher=publisher,
                    )
                self.assertEqual(observed_calls, [0])
                self.assertEqual(publisher.calls, 0)


    def test_all_twelve_role_bundles_are_checksum_verified_in_a_fresh_process(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "agency_os.runtime_bundles"],
            check=True, capture_output=True, text=True,
        )
        evidence = json.loads(completed.stdout)
        self.assertEqual(evidence["bundle_count"], 12)
        self.assertEqual(
            {item["bundle_verification_status"] for item in evidence["roles"]},
            {"checksum_verified_in_fresh_process"},
        )
        self.assertEqual(evidence["target_runtime_evidence"], "pending_live_activation")


if __name__ == "__main__":
    unittest.main()
