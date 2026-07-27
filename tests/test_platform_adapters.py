from __future__ import annotations

import copy
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from agency_os.contracts import ContractError, canonical_bytes, finalize_record
from agency_os.platform_adapters import (
    EvidenceStoreError,
    FictionalBuzzAdapter,
    FictionalPaperclipAdapter,
    PlatformAdapterError,
    SQLiteTenantEvidenceStore,
    make_approver_policy,
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

    def _policy(
        self,
        *,
        revision: int = 1,
        permitted: tuple[str, ...] = ("human_owner",),
        previous_checksum: str | None = None,
    ) -> dict:
        return make_approver_policy(
            policy_id="brand_approval_policy",
            brand_id="brand_lantern",
            revision=revision,
            permitted_approver_ids=permitted,
            issued_by=self.director.actor_id,
            effective_at=(self.now - timedelta(minutes=1)).isoformat(),
            previous_policy_checksum=previous_checksum,
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
        policy = self.paperclip.register_approver_policy(
            self.director, self._policy()
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
            policy_id=policy["policy_id"],
            policy_revision=policy["revision"],
            policy_checksum=policy["content_checksum"],
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
            policy_id=policy["policy_id"],
            policy_revision=policy["revision"],
            policy_checksum=policy["content_checksum"],
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

    def test_approver_catalogue_and_policy_drift_control_closure(self) -> None:
        self.paperclip.create_task(
            self.director, self._task("issue_policy", approval_required=True)
        )
        self.evidence.put(
            self.strategist,
            self._evidence("evidence_policy", issue_id="issue_policy"),
        )
        policy_1 = self.paperclip.register_approver_policy(
            self.director, self._policy()
        )
        alternate_policy = make_approver_policy(
            policy_id="alternate_brand_policy",
            brand_id="brand_lantern",
            revision=1,
            permitted_approver_ids=("unlisted_human",),
            issued_by=self.director.actor_id,
            effective_at=(self.now - timedelta(minutes=1)).isoformat(),
        )
        before_alternate_audit = self.paperclip.audit_events(self.director)
        with self.assertRaises(ContractError):
            self.paperclip.register_approver_policy(
                self.director, alternate_policy
            )
        self.assertEqual(
            self.paperclip.audit_events(self.director), before_alternate_audit
        )
        current = self._advance_to_in_progress("issue_policy")
        unlisted = Principal("unlisted_human", "human-approver", "brand_lantern")
        before_task = self.paperclip.get_task(self.director, "issue_policy")
        before_audit = self.paperclip.audit_events(self.director)
        with self.assertRaises(AuthorizationError):
            self.paperclip.record_approval(
                unlisted,
                approval_id="approval_unlisted",
                issue_id="issue_policy",
                expected_task_checksum=current["content_checksum"],
                policy_id=policy_1["policy_id"],
                policy_revision=policy_1["revision"],
                policy_checksum=policy_1["content_checksum"],
                decision="APPROVED",
                decided_at=self.now.isoformat(),
                expires_at=(self.now + timedelta(minutes=10)).isoformat(),
            )
        self.assertEqual(
            self.paperclip.get_task(self.director, "issue_policy"), before_task
        )
        self.assertEqual(self.paperclip.audit_events(self.director), before_audit)

        with self.assertRaises(ContractError):
            self.paperclip.record_approval(
                self.approver,
                approval_id="approval_future",
                issue_id="issue_policy",
                expected_task_checksum=current["content_checksum"],
                policy_id=policy_1["policy_id"],
                policy_revision=policy_1["revision"],
                policy_checksum=policy_1["content_checksum"],
                decision="APPROVED",
                decided_at=(self.now + timedelta(minutes=1)).isoformat(),
                expires_at=(self.now + timedelta(minutes=10)).isoformat(),
            )
        self.assertEqual(self.paperclip.audit_events(self.director), before_audit)

        self.paperclip.record_approval(
            self.approver,
            approval_id="approval_policy_1",
            issue_id="issue_policy",
            expected_task_checksum=current["content_checksum"],
            policy_id=policy_1["policy_id"],
            policy_revision=policy_1["revision"],
            policy_checksum=policy_1["content_checksum"],
            decision="APPROVED",
            decided_at=self.now.isoformat(),
            expires_at=(self.now + timedelta(minutes=10)).isoformat(),
        )
        policy_2 = self.paperclip.register_approver_policy(
            self.director,
            self._policy(
                revision=2,
                previous_checksum=policy_1["content_checksum"],
            ),
        )
        before_drift_audit = self.paperclip.audit_events(self.director)
        with self.assertRaises(ContractError):
            self.paperclip.close_task(
                self.director,
                "issue_policy",
                current["content_checksum"],
                evidence_refs=("evidence_policy",),
                approval_id="approval_policy_1",
            )
        self.assertEqual(
            self.paperclip.get_task(self.director, "issue_policy"), current
        )
        self.assertEqual(
            self.paperclip.audit_events(self.director), before_drift_audit
        )

        self.paperclip.record_approval(
            self.approver,
            approval_id="approval_policy_2",
            issue_id="issue_policy",
            expected_task_checksum=current["content_checksum"],
            policy_id=policy_2["policy_id"],
            policy_revision=policy_2["revision"],
            policy_checksum=policy_2["content_checksum"],
            decision="APPROVED",
            decided_at=self.now.isoformat(),
            expires_at=(self.now + timedelta(minutes=10)).isoformat(),
        )
        closed = self.paperclip.close_task(
            self.director,
            "issue_policy",
            current["content_checksum"],
            evidence_refs=("evidence_policy",),
            approval_id="approval_policy_2",
        )
        self.assertEqual(closed["status"], "done")

    def test_legacy_approval_without_policy_binding_is_rejected(self) -> None:
        self.paperclip.create_task(
            self.director, self._task("issue_legacy", approval_required=True)
        )
        self.evidence.put(
            self.strategist,
            self._evidence("evidence_legacy", issue_id="issue_legacy"),
        )
        policy = self.paperclip.register_approver_policy(
            self.director, self._policy()
        )
        current = self._advance_to_in_progress("issue_legacy")
        legacy = finalize_record(
            {
                "schema_version": "1.0",
                "artifact_type": "paperclip_task_approval",
                "approval_id": "approval_legacy",
                "brand_id": "brand_lantern",
                "paperclip_issue_id": "issue_legacy",
                "task_checksum": current["content_checksum"],
                "decision": "APPROVED",
                "approver_id": self.approver.actor_id,
                "authority_role": self.approver.role_id,
                "decided_at": self.now.isoformat(),
                "expires_at": (self.now + timedelta(minutes=10)).isoformat(),
            }
        )
        forged = finalize_record(
            {
                "schema_version": "1.0",
                "artifact_type": "paperclip_task_approval",
                "approval_id": "approval_forged_actor",
                "brand_id": "brand_lantern",
                "paperclip_issue_id": "issue_legacy",
                "task_checksum": current["content_checksum"],
                "policy_id": policy["policy_id"],
                "policy_revision": policy["revision"],
                "policy_checksum": policy["content_checksum"],
                "decision": "APPROVED",
                "approver_id": "unlisted_human",
                "authority_role": "human-approver",
                "decided_at": self.now.isoformat(),
                "expires_at": (self.now + timedelta(minutes=10)).isoformat(),
            }
        )
        connection = sqlite3.connect(self.database_path)
        try:
            connection.execute(
                """
                INSERT INTO paperclip_approvals (
                    brand_id, approval_id, issue_id, task_checksum,
                    record_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "brand_lantern",
                    "approval_legacy",
                    "issue_legacy",
                    current["content_checksum"],
                    canonical_bytes(legacy).decode("utf-8"),
                    self.now.isoformat(),
                ),
            )
            connection.execute(
                """
                INSERT INTO paperclip_approvals (
                    brand_id, approval_id, issue_id, task_checksum,
                    record_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "brand_lantern",
                    "approval_forged_actor",
                    "issue_legacy",
                    current["content_checksum"],
                    canonical_bytes(forged).decode("utf-8"),
                    self.now.isoformat(),
                ),
            )
            connection.commit()
        finally:
            connection.close()
        before_audit = self.paperclip.audit_events(self.director)
        with self.assertRaises(ContractError):
            self.paperclip.close_task(
                self.director,
                "issue_legacy",
                current["content_checksum"],
                evidence_refs=("evidence_legacy",),
                approval_id="approval_legacy",
            )
        with self.assertRaises(ContractError):
            self.paperclip.close_task(
                self.director,
                "issue_legacy",
                current["content_checksum"],
                evidence_refs=("evidence_legacy",),
                approval_id="approval_forged_actor",
            )
        self.assertEqual(
            self.paperclip.get_task(self.director, "issue_legacy"), current
        )
        self.assertEqual(self.paperclip.audit_events(self.director), before_audit)

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
        buzz = FictionalBuzzAdapter(self.paperclip, clock=lambda: self.now)
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
        restarted = FictionalPaperclipAdapter(
            self.database_path, clock=lambda: self.now
        )
        restarted_buzz = FictionalBuzzAdapter(restarted, clock=lambda: self.now)
        decision = restarted_buzz.collect_decision(
            self.director,
            context_id="context_asset",
            decision_id="decision_asset",
            summary="Retain the cited fictional claim; this is not an approval.",
            source_event_ids=("buzz_event_1", "buzz_event_2"),
        )

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
        forged_author = copy.deepcopy(decision)
        forged_author["decision_id"] = "decision_forged_author"
        forged_author["recorded_by"] = "another_actor"
        forged_author = finalize_record(forged_author)
        with self.assertRaises(AuthorizationError):
            restarted.record_buzz_decision(self.director, forged_author)

    def test_buzz_deadline_is_enforced_by_buzz_and_paperclip(self) -> None:
        authority_time = [self.now]
        paperclip = FictionalPaperclipAdapter(
            self.database_path, clock=lambda: authority_time[0]
        )
        paperclip.create_task(self.director, self._task("issue_asset"))
        buzz = FictionalBuzzAdapter(paperclip, clock=lambda: authority_time[0])
        context = make_buzz_context_packet(
            context_id="context_expiring",
            brand_id="brand_lantern",
            campaign_id="campaign_launch",
            paperclip_issue_id="issue_asset",
            purpose="Resolve a fictional source dispute.",
            decision_needed="Choose a supported claim before expiry.",
            participants=("agent_director", "agent_strategist"),
            source_artifact_ids=(),
            constraints=("No late decisions.",),
            deadline=(self.now + timedelta(minutes=1)).isoformat(),
            exit_condition="Record a decision before the deadline.",
            created_by=self.director.actor_id,
            created_at=self.now.isoformat(),
        )
        buzz.post_context(self.director, context)
        before_audit = paperclip.audit_events(self.director)
        authority_time[0] = self.now + timedelta(minutes=2)

        with self.assertRaises(ContractError):
            paperclip.record_buzz_context(self.director, context)
        with self.assertRaises(ContractError):
            buzz.collect_decision(
                self.director,
                context_id="context_expiring",
                decision_id="decision_late",
                summary="This late decision must not persist.",
                source_event_ids=("buzz_event_late",),
            )
        with self.assertRaises(KeyError):
            paperclip.get_buzz_decision(self.director, "decision_late")

        backdated = finalize_record(
            {
                "schema_version": "1.0",
                "artifact_type": "buzz_decision_summary",
                "decision_id": "decision_backdated",
                "brand_id": "brand_lantern",
                "campaign_id": "campaign_launch",
                "paperclip_issue_id": "issue_asset",
                "context_id": "context_expiring",
                "context_checksum": context["content_checksum"],
                "summary": "A direct, backdated write must also fail.",
                "source_event_ids": ["buzz_event_backdated"],
                "recorded_by": self.director.actor_id,
                "recorded_at": (self.now + timedelta(seconds=30)).isoformat(),
            }
        )
        with self.assertRaises(ContractError):
            paperclip.record_buzz_decision(self.director, backdated)
        with self.assertRaises(KeyError):
            paperclip.get_buzz_decision(self.director, "decision_backdated")
        self.assertEqual(paperclip.audit_events(self.director), before_audit)

    def test_archived_buzz_context_remains_closed_after_restart(self) -> None:
        self.paperclip.create_task(self.director, self._task("issue_asset"))
        buzz = FictionalBuzzAdapter(self.paperclip, clock=lambda: self.now)
        context = make_buzz_context_packet(
            context_id="context_archived",
            brand_id="brand_lantern",
            campaign_id="campaign_launch",
            paperclip_issue_id="issue_asset",
            purpose="Resolve a fictional source dispute.",
            decision_needed="Choose a supported claim.",
            participants=("agent_director", "agent_strategist"),
            source_artifact_ids=(),
            constraints=(),
            deadline=(self.now + timedelta(hours=1)).isoformat(),
            exit_condition="Archive the context.",
            created_by=self.director.actor_id,
            created_at=self.now.isoformat(),
        )
        buzz.post_context(self.director, context)
        buzz.archive(self.director, "context_archived")

        restarted = FictionalPaperclipAdapter(
            self.database_path, clock=lambda: self.now
        )
        restarted_buzz = FictionalBuzzAdapter(restarted, clock=lambda: self.now)
        with self.assertRaises(ContractError):
            restarted_buzz.collect_decision(
                self.director,
                context_id="context_archived",
                decision_id="decision_archived",
                summary="An archived context cannot accept a decision.",
                source_event_ids=("buzz_event_archived",),
            )
        with self.assertRaises(KeyError):
            restarted.get_buzz_decision(self.director, "decision_archived")

    def test_buzz_cross_tenant_and_non_authority_writeback_are_denied(self) -> None:
        self.paperclip.create_task(self.director, self._task("issue_asset"))
        buzz = FictionalBuzzAdapter(self.paperclip, clock=lambda: self.now)
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
