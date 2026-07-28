from __future__ import annotations

import json
import subprocess
import sys
import unittest

from agency_os.contracts import verify_record
from agency_os.core_workflow import CORE_RUNTIME_ROLES, run_core_workflow


class CoreWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.result = run_core_workflow()

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
        self.assertEqual(result.vertical_slice.publisher.calls, 1)
        self.assertFalse(result.external_writes)

    def test_buzz_decision_is_written_back_and_never_becomes_task_authority(self) -> None:
        result = self.result
        producer_id = result.tasks_by_role["content-producer"]["id"]
        messages = next(iter(result.buzz_transport.channel_messages.values()))
        self.assertEqual(messages[-1]["content"]["authority"], "non_authoritative_until_written_to_paperclip")
        comments = result.paperclip_transport.comments[producer_id]
        self.assertTrue(any("Buzz decision write-back" in item["body"] for item in comments))
        self.assertEqual(result.paperclip_transport.issues[producer_id]["status"], "done")

    def test_operator_projection_is_read_only_and_paperclip_derived(self) -> None:
        projection = self.result.operator_projection
        self.assertEqual(projection["authority"], "paperclip")
        self.assertEqual(projection["projection"], "read_only")
        self.assertEqual(projection["task_counts"], {"done": 8})
        self.assertEqual(len(projection["tasks"]), 8)
        self.assertEqual(projection["approvals"][0]["status"], "approved")

    def test_all_eight_role_bundles_load_in_a_fresh_process(self) -> None:
        completed = subprocess.run(
            [sys.executable, "-m", "agency_os.runtime_bundles"],
            check=True, capture_output=True, text=True,
        )
        evidence = json.loads(completed.stdout)
        self.assertEqual(evidence["bundle_count"], 8)
        self.assertEqual(
            {item["reference_loader_status"] for item in evidence["roles"]},
            {"loaded_in_fresh_process"},
        )
        self.assertEqual(evidence["target_runtime_evidence"], "pending_runtime_installation")


if __name__ == "__main__":
    unittest.main()
