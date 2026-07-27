from __future__ import annotations

import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agency_os.contracts import ContractError, finalize_record
from agency_os.platform_adapters import (
    EvidenceStoreError,
    FictionalBuzzAdapter,
    FictionalPaperclipAdapter,
    PlatformAdapterError,
    SQLiteTenantEvidenceStore,
    make_buzz_context_packet,
    make_evidence_record,
    make_paperclip_task,
)
from agency_os.store import AuthorizationError, Principal


class PlatformAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.database_path = Path(temporary_directory.name) / "platform.sqlite3"
        self.now = datetime(2026, 7, 27, 20, 45, tzinfo=timezone.utc)
        self.paperclip = FictionalPaperclipAdapter(
            self.database_path, clock=lambda: self.now
        )
        self.evidence = SQLiteTenantEvidenceStore(self.database_path)
        self.director = Principal(
            "agent_director", "agency-director", "brand_lantern"
        )
        self.foreign_director = Principal(
            "agent_director_ember", "agency-director", "brand_ember"
        )
        self.approver = Principal(
            "human_owner", "human-approver", "brand_lantern"
        )
        self.strategist = Principal(
            "agent_strategist", "search-content-strategist", "brand_lantern"
        )

    def _task(
        self,
        issue_id: str,
        *,
        dependencies: tuple[str, ...] = (),
        approval_required: bool = False,
        budget_limit_minor: int = 1_000,
    ) -> dict:
        return make_paperclip_task(
            issue_id=issue_id,
            brand_id="brand_lantern",
            campaign_id="campaign_launch",
            task_type="fictional_content_task",
            title=f"Task {issue_id}",
            dependencies=dependencies,
            acceptance_criteria=("retains evidence",),
            budget_limit_minor=budget_limit_minor,
            created_by=self.director.actor_id,
            approval_required=approval_required,
            created_at=self.now.isoformat(),
        )

    def _advance_to_in_progress(self, issue_id: str) -> dict:
        current = self.paperclip.get_task(self.director, issue_id)
        current = self.paperclip.set_status(
            self.director, issue_id, current["content_checksum"], "ready"
        )
        return self.paperclip.set_status(
            self.director, issue_id, current["content_checksum"], "in_progress"
        )

    def _evidence(
        self,
        evidence_id: str = "evidence_primary",
        *,
        issue_id: str = "issue_asset",
    ) -> dict:
        return make_evidence_record(
            evidence_id=evidence_id,
            brand_id="brand_lantern",
            paperclip_issue_id=issue_id,
            source_ref="fixture://research/primary-source",
            source_class="primary",
            retrieved_at=self.now.isoformat(),
            claim="The fictional source supports the task.",
            extract="A short fictional extract.",
            confidence=0.9,
            created_by=self.strategist.actor_id,
        )

    def test_dependencies_budget_closure_and_restart_are_authoritative(self) -> None:
        self.paperclip.create_task(self.director, self._task("issue_research"))
        self.paperclip.create_task(
            self.director,
            self._task("issue_asset", dependencies=("issue_research",)),
        )
        self.evidence.put(
            self.strategist,
            self._evidence("evidence_research", issue_id="issue_research"),
        )
        self.evidence.put(self.strategist, self._evidence())

        asset = self.paperclip.get_task(self.director, "issue_asset")
        with self.assertRaises(ContractError):
            self.paperclip.set_status(
                self.director,
                "issue_asset",
                asset["content_checksum"],
                "ready",
            )

        research = self._advance_to_in_progress("issue_research")
        self.paperclip.close_task(
            self.director,
            "issue_research",
            research["content_checksum"],
            evidence_refs=("evidence_research",),
        )
        asset = self._advance_to_in_progress("issue_asset")
        asset = self.paperclip.record_spend(
            self.director, "issue_asset", asset["content_checksum"], 750
        )
        with self.assertRaises(ContractError):
            self.paperclip.record_spend(
                self.director, "issue_asset", asset["content_checksum"], 251
            )
        closed = self.paperclip.close_task(
            self.director,
            "issue_asset",
            asset["content_checksum"],
            evidence_refs=("evidence_primary",),
        )

        restarted = FictionalPaperclipAdapter(
            self.database_path, clock=lambda: self.now
        )
        self.assertEqual(restarted.get_task(self.director, "issue_asset"), closed)
        self.assertEqual(closed["status"], "done")
        self.assertEqual(closed["budget"]["spent_minor"], 750)

    def test_approval_is_exact_fresh_and_required_for_closure(self) -> None:
        self.paperclip.create_task(
            self.director, self._task("issue_approval", approval_required=True)
        )
        current = self._advance_to_in_progress("issue_approval")
        with self.assertRaises(ContractError):
            self.paperclip.close_task(
                self.director,
                "issue_approval",
                current["content_checksum"],
                evidence_refs=("evidence_approval",),
            )

        rejected = self.paperclip.record_approval(
            self.approver,
            approval_id="approval_rejected",
            issue_id="issue_approval",
            expected_task_checksum=current["content_checksum"],
            decision="REJECTED",
            decided_at=self.now.isoformat(),
            expires_at=(self.now + timedelta(minutes=10)).isoformat(),
        )
        self.assertEqual(rejected["decision"], "REJECTED")
        with self.assertRaises(ContractError):
            self.paperclip.close_task(
                self.director,
                "issue_approval",
                current["content_checksum"],
                evidence_refs=("evidence_approval",),
                approval_id="approval_rejected",
            )

        self.paperclip.record_approval(
            self.approver,
            approval_id="approval_allowed",
            issue_id="issue_approval",
            expected_task_checksum=current["content_checksum"],
            decision="APPROVED",
            decided_at=self.now.isoformat(),
            expires_at=(self.now + timedelta(minutes=10)).isoformat(),
        )
        changed = self.paperclip.record_spend(
            self.director, "issue_approval", current["content_checksum"], 1
        )
        with self.assertRaises(ContractError):
            self.paperclip.close_task(
                self.director,
                "issue_approval",
                changed["content_checksum"],
                evidence_refs=("evidence_approval",),
                approval_id="approval_allowed",
            )

    def test_task_tenant_and_optimistic_concurrency_boundaries_fail_closed(self) -> None:
        task = self._task("issue_asset")
        self.paperclip.create_task(self.director, task)
        with self.assertRaises(KeyError):
            self.paperclip.get_task(self.foreign_director, "issue_asset")
        with self.assertRaises(AuthorizationError):
            self.paperclip.create_task(self.foreign_director, task)

        current = self.paperclip.set_status(
            self.director, "issue_asset", task["content_checksum"], "ready"
        )
        with self.assertRaises(ContractError):
            self.paperclip.set_status(
                self.director,
                "issue_asset",
                task["content_checksum"],
                "in_progress",
            )
        self.assertEqual(
            self.paperclip.get_task(self.director, "issue_asset"), current
        )

    def test_buzz_decision_is_written_to_paperclip_without_task_mutation(self) -> None:
        self.paperclip.create_task(self.director, self._task("issue_asset"))
        task_before = self.paperclip.get_task(self.director, "issue_asset")
        buzz = FictionalBuzzAdapter(self.paperclip)
        context = make_buzz_context_packet(
            context_id="context_asset",
            brand_id="brand_lantern",
            campaign_id="campaign_launch",
            paperclip_issue_id="issue_asset",
            purpose="Resolve a fictional source dispute.",
            decision_needed="Choose which supported claim to retain.",
            participants=("agent_director", "agent_strategist"),
            source_artifact_ids=("evidence_primary",),
            constraints=("No task-state changes in Buzz.",),
            deadline=(self.now + timedelta(hours=1)).isoformat(),
            exit_condition="Decision is recorded in Paperclip.",
            created_by=self.director.actor_id,
            created_at=self.now.isoformat(),
        )
        buzz.post_context(self.director, context)
        decision = buzz.collect_decision(
            self.director,
            context_id="context_asset",
            decision_id="decision_asset",
            summary="Retain the cited fictional claim; this is not an approval.",
            source_event_ids=("buzz_event_1", "buzz_event_2"),
            recorded_at=self.now.isoformat(),
        )

        restarted = FictionalPaperclipAdapter(self.database_path)
        self.assertEqual(
            restarted.get_buzz_context(self.director, "context_asset"), context
        )
        self.assertEqual(
            restarted.get_buzz_decision(self.director, "decision_asset"), decision
        )
        self.assertEqual(
            restarted.get_task(self.director, "issue_asset"), task_before
        )
        forged = copy.deepcopy(decision)
        forged["decision_id"] = "decision_forged"
        forged["context_checksum"] = "sha256:" + "0" * 64
        forged = finalize_record(forged)
        with self.assertRaises(ContractError):
            restarted.record_buzz_decision(self.director, forged)

    def test_buzz_cross_tenant_and_non_authority_writeback_are_denied(self) -> None:
        self.paperclip.create_task(self.director, self._task("issue_asset"))
        buzz = FictionalBuzzAdapter(self.paperclip)
        context = make_buzz_context_packet(
            context_id="context_asset",
            brand_id="brand_lantern",
            campaign_id="campaign_launch",
            paperclip_issue_id="issue_asset",
            purpose="Discuss a fictional source.",
            decision_needed="Choose a supported claim.",
            participants=("agent_director", "agent_strategist"),
            source_artifact_ids=(),
            constraints=(),
            deadline=(self.now + timedelta(hours=1)).isoformat(),
            exit_condition="Write a decision summary.",
            created_by=self.director.actor_id,
            created_at=self.now.isoformat(),
        )
        with self.assertRaises(AuthorizationError):
            buzz.post_context(self.foreign_director, context)
        buzz.post_context(self.director, context)
        with self.assertRaises(AuthorizationError):
            buzz.collect_decision(
                self.strategist,
                context_id="context_asset",
                decision_id="decision_asset",
                summary="Attempted non-authoritative decision.",
                source_event_ids=("buzz_event_1",),
            )
        with self.assertRaises(KeyError):
            self.paperclip.get_buzz_decision(
                self.foreign_director, "decision_asset"
            )

    def test_evidence_is_persistent_immutable_and_tenant_isolated(self) -> None:
        self.paperclip.create_task(self.director, self._task("issue_asset"))
        evidence = self._evidence()
        self.evidence.put(self.strategist, evidence)
        restarted = SQLiteTenantEvidenceStore(self.database_path)
        self.assertEqual(restarted.get(self.strategist, "evidence_primary"), evidence)
        self.assertEqual(
            restarted.list_for_issue(self.strategist, "issue_asset"), [evidence]
        )
        with self.assertRaises(KeyError):
            restarted.get(self.foreign_director, "evidence_primary")
        with self.assertRaises(AuthorizationError):
            restarted.put(self.foreign_director, evidence)

        changed = copy.deepcopy(evidence)
        changed["claim"] = "A changed claim."
        changed = finalize_record(changed)
        with self.assertRaises(ContractError):
            restarted.put(self.strategist, changed)

    def test_evidence_wrong_role_and_actor_are_denied(self) -> None:
        evidence = self._evidence()
        with self.assertRaises(ContractError):
            self.evidence.put(self.strategist, evidence)
        publisher = Principal(
            "agent_publisher", "publishing-operator", "brand_lantern"
        )
        with self.assertRaises(AuthorizationError):
            self.evidence.put(publisher, evidence)
        forged = copy.deepcopy(evidence)
        forged["created_by"] = "another_actor"
        forged = finalize_record(forged)
        with self.assertRaises(AuthorizationError):
            self.evidence.put(self.strategist, forged)

    def test_replaced_storage_identity_is_rejected_by_both_authorities(self) -> None:
        self.paperclip.create_task(self.director, self._task("issue_asset"))
        replacement_path = self.database_path.with_name("replacement.sqlite3")
        FictionalPaperclipAdapter(replacement_path)
        replacement_path.replace(self.database_path)

        with self.assertRaises(PlatformAdapterError):
            self.paperclip.get_task(self.director, "issue_asset")
        with self.assertRaises(EvidenceStoreError):
            self.evidence.get(self.strategist, "evidence_primary")

    def test_audit_is_persistent_and_tenant_scoped(self) -> None:
        self.paperclip.create_task(self.director, self._task("issue_asset"))
        self.evidence.put(self.strategist, self._evidence())
        events = FictionalPaperclipAdapter(self.database_path).audit_events(
            self.director
        )
        self.assertEqual(
            [event["event_type"] for event in events],
            ["paperclip.task.created", "evidence.recorded"],
        )
        self.assertEqual(
            FictionalPaperclipAdapter(self.database_path).audit_events(
                self.foreign_director
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
