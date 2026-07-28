from __future__ import annotations

import copy
import hashlib
import hmac
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import agency_os
from agency_os._approval_authority import (
    _FictionalApprovalAuthority,
    _FictionalRecoveryAuthority,
)
from agency_os.contracts import (
    ContractError,
    canonical_bytes,
    canonical_checksum,
    finalize_record,
)
from agency_os.platform_authority_host import (
    PlatformAuthorityClient,
    PlatformAuthorityUnavailable,
    TenantWorkQueueClient,
    _provision_platform_authority_host,
)
from agency_os.platform_adapters import (
    _AuthorityPaperclipAdapter,
    _SQLiteArtifactDeletionLedger,
    ArtifactStoreError,
    EvidenceStoreError,
    FictionalBuzzAdapter,
    PlatformAdapterError,
    WorkQueueError,
    make_approver_policy,
    make_buzz_context_packet,
    make_evidence_record,
    make_paperclip_task,
    make_work_queue_item,
)
from agency_os.store import AuthorizationError, Principal


class PlatformAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_directory.cleanup)
        self.database_path = Path(temporary_directory.name) / "platform.sqlite3"
        self.deletion_ledger_path = (
            Path(temporary_directory.name) / "artifact-deletions.sqlite3"
        )
        self.now = datetime(2026, 7, 27, 20, 45, tzinfo=timezone.utc)
        self.director = Principal(
            "agent_director", "agency-director", "brand_lantern"
        )
        self.foreign_director = Principal(
            "agent_director_ember", "agency-director", "brand_ember"
        )
        self.approver = Principal(
            "human_owner", "human-approver", "brand_lantern"
        )
        self.unlisted_approver = Principal(
            "unlisted_human", "human-approver", "brand_lantern"
        )
        self.strategist = Principal(
            "agent_strategist", "search-content-strategist", "brand_lantern"
        )
        self.publisher = Principal(
            "agent_publisher", "publishing-operator", "brand_lantern"
        )
        self.reviewer = Principal(
            "agent_reviewer", "platform-assurance-reviewer", "brand_lantern"
        )
        self.provisioned_principals = (
            self.director,
            self.foreign_director,
            self.approver,
            self.unlisted_approver,
            self.strategist,
            self.publisher,
            self.reviewer,
        )
        self.approval_signing_key = os.urandom(32)
        self.platform_host = _provision_platform_authority_host(
            self.database_path,
            deletion_ledger_path=self.deletion_ledger_path,
            initialize_deletion_ledger=True,
            authority_id="fictional_paperclip_approval_authority",
            approval_signing_key=self.approval_signing_key,
            initial_time=self.now,
            principals=self.provisioned_principals,
        )
        self.addCleanup(self.platform_host.close)
        self.paperclip = self.platform_host.client(self.director)
        self.approval_client = self.platform_host.client(self.approver)
        self.unlisted_approval_client = self.platform_host.client(
            self.unlisted_approver
        )
        self.foreign_paperclip = self.platform_host.client(self.foreign_director)
        self.strategist_paperclip = self.platform_host.client(self.strategist)
        self.publisher_paperclip = self.platform_host.client(self.publisher)
        self.evidence = self.strategist_paperclip.evidence()
        self.artifacts = self.paperclip.artifacts()
        self.strategist_artifacts = self.strategist_paperclip.artifacts()
        self.foreign_artifacts = self.foreign_paperclip.artifacts()

    def _task(
        self,
        issue_id: str,
        *,
        dependencies: tuple[str, ...] = (),
        approval_required: bool = False,
        budget_limit_minor: int = 1_000,
        brand_id: str = "brand_lantern",
        created_by: str | None = None,
    ) -> dict:
        return make_paperclip_task(
            issue_id=issue_id,
            brand_id=brand_id,
            campaign_id="campaign_launch",
            task_type="fictional_content_task",
            title=f"Task {issue_id}",
            dependencies=dependencies,
            acceptance_criteria=("retains evidence",),
            budget_limit_minor=budget_limit_minor,
            created_by=created_by or self.director.actor_id,
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

    def _work_item(
        self,
        work_item_id: str,
        task: dict,
        *,
        work_kind: str = "internal",
        worker_role: str = "search-content-strategist",
        max_attempts: int = 2,
        brand_id: str = "brand_lantern",
        created_by: str | None = None,
    ) -> dict:
        return make_work_queue_item(
            work_item_id=work_item_id,
            brand_id=brand_id,
            paperclip_issue_id=task["paperclip_issue_id"],
            paperclip_task_checksum=task["content_checksum"],
            work_kind=work_kind,
            worker_role=worker_role,
            payload={"fictional_operation": "draft-local-artifact"},
            max_attempts=max_attempts,
            created_by=created_by or self.director.actor_id,
            created_at=self.now.isoformat(),
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

    def _artifact(
        self,
        artifact_id: str,
        *,
        brand_id: str = "brand_lantern",
    ) -> dict:
        return finalize_record(
            {
                "schema_version": "1.0",
                "artifact_type": "content_brief",
                "artifact_id": artifact_id,
                "brand_id": brand_id,
                "campaign_id": "campaign_launch",
                "created_by": self.strategist.actor_id,
                "summary": "A fictional durable content brief.",
            }
        )

    def _learning(
        self,
        learning_record_id: str,
        *,
        brand_id: str = "brand_lantern",
    ) -> dict:
        dispositioned_by = (
            self.director.actor_id
            if brand_id == self.director.brand_id
            else self.foreign_director.actor_id
        )
        return finalize_record(
            {
                "schema_version": "1.0",
                "artifact_type": "learning_record",
                "learning_record_id": learning_record_id,
                "version": 1,
                "brand_id": brand_id,
                "validation_status": "validated",
                "lifecycle_status": "active",
                "reuse_scope": "brand-only",
                "expected_result": "The fictional first attempt succeeds.",
                "actual_result": "The fictional first attempt failed.",
                "attempted_approach": "approach-a",
                "validated_correction": "approach-b",
                "evidence_refs": ["evidence_fixture"],
                "confidence": 0.9,
                "limitations": [],
                "fresh_until": (self.now + timedelta(days=1)).isoformat(),
                "reviewed_at": self.now.isoformat(),
                "supersedes": None,
                "dispositioned_by": dispositioned_by,
            }
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

    def test_durable_artifacts_and_learning_survive_restart(self) -> None:
        brief = self._artifact("brief_durable")
        learning = self._learning("learning_durable")
        self.assertEqual(
            self.strategist_artifacts.put(self.strategist, brief),
            "brief_durable",
        )
        self.assertEqual(
            self.artifacts.put(self.director, learning),
            "learning_durable",
        )

        changed = copy.deepcopy(brief)
        changed["summary"] = "A conflicting replacement."
        changed = finalize_record(changed)
        with self.assertRaises(ContractError):
            self.strategist_artifacts.put(self.strategist, changed)
        with self.assertRaises(KeyError):
            self.foreign_artifacts.get(self.foreign_director, "brief_durable")
        with self.assertRaises(AuthorizationError):
            self.publisher_paperclip.artifacts().get(
                self.publisher, "learning_durable"
            )

        self.platform_host.close()
        restarted = _provision_platform_authority_host(
            self.database_path,
            deletion_ledger_path=self.deletion_ledger_path,
            authority_id="fictional_paperclip_approval_authority",
            approval_signing_key=self.approval_signing_key,
            initial_time=self.now,
            principals=self.provisioned_principals,
        )
        self.addCleanup(restarted.close)
        restarted_director = restarted.client(self.director).artifacts()
        restarted_strategist = restarted.client(self.strategist).artifacts()
        self.assertEqual(
            restarted_strategist.get(self.strategist, "brief_durable"), brief
        )
        self.assertEqual(
            restarted_director.get(self.director, "learning_durable"), learning
        )
        self.assertEqual(
            [
                record["learning_record_id"]
                for record in restarted_director.active_learning(self.director)
            ],
            ["learning_durable"],
        )

    def test_artifact_export_restore_is_integrity_and_tenant_bound(self) -> None:
        brief = self._artifact("brief_backup")
        brief["summary"] = "fictional-export-payload-" + "x" * 32_768
        brief = finalize_record(brief)
        learning = self._learning("learning_backup")
        self.strategist_artifacts.put(self.strategist, brief)
        self.artifacts.put(self.director, learning)
        tenant_export = self.artifacts.export_tenant(self.director)
        self.assertEqual(tenant_export["record_count"], 2)
        self.assertEqual(
            set(tenant_export["records"]),
            {"brief_backup", "learning_backup"},
        )
        self.assertEqual(tenant_export["exported_at"], self.now.isoformat())
        self.assertEqual(
            tenant_export["export_attestation"]["authority_id"],
            "fictional_paperclip_approval_authority",
        )
        self.assertEqual(
            tenant_export["export_attestation"]["brand_id"],
            self.director.brand_id,
        )
        publisher_artifacts = self.publisher_paperclip.artifacts()
        with self.assertRaises(AuthorizationError):
            publisher_artifacts.export_tenant(self.publisher)
        with self.assertRaises(AuthorizationError):
            publisher_artifacts.delete_tenant(
                self.publisher,
                tenant_export["export_checksum"],
            )

        restore_path = self.database_path.with_name("restore.sqlite3")
        restored_host = _provision_platform_authority_host(
            restore_path,
            deletion_ledger_path=self.deletion_ledger_path,
            authority_id="fictional_paperclip_approval_authority",
            approval_signing_key=self.approval_signing_key,
            initial_time=self.now,
            principals=self.provisioned_principals,
        )
        self.addCleanup(restored_host.close)
        restored_paperclip = restored_host.client(self.director)
        restored = restored_paperclip.artifacts()
        foreign_restored = restored_host.client(self.foreign_director).artifacts()

        def recompute_public_checksums(value: dict) -> None:
            for record in value["records"].values():
                refreshed = finalize_record(record)
                record.clear()
                record.update(refreshed)
            payload = {
                key: value[key]
                for key in (
                    "schema_version",
                    "brand_id",
                    "record_count",
                    "records",
                    "provenance",
                )
            }
            value["export_checksum"] = canonical_checksum(payload)
            value["export_attestation"]["export_checksum"] = value[
                "export_checksum"
            ]

        tampered = copy.deepcopy(tenant_export)
        tampered["records"]["brief_backup"]["summary"] = "attacker altered content"
        recompute_public_checksums(tampered)
        with self.assertRaises(ContractError):
            restored.restore_tenant(self.director, tampered)

        forged_actor = copy.deepcopy(tenant_export)
        forged_actor["provenance"]["brief_backup"]["actor_id"] = (
            "invented_strategist"
        )
        recompute_public_checksums(forged_actor)
        with self.assertRaises(ContractError):
            restored.restore_tenant(self.director, forged_actor)

        forged_role = copy.deepcopy(tenant_export)
        forged_role["provenance"]["brief_backup"]["role_id"] = (
            "publishing-operator"
        )
        recompute_public_checksums(forged_role)
        with self.assertRaises(ContractError):
            restored.restore_tenant(self.director, forged_role)

        changed_attestation = copy.deepcopy(tenant_export)
        changed_attestation["export_attestation"]["authority_id"] = (
            "attacker_authority"
        )
        with self.assertRaises(ContractError):
            restored.restore_tenant(self.director, changed_attestation)

        changed_time = copy.deepcopy(tenant_export)
        changed_time["exported_at"] = (self.now + timedelta(minutes=1)).isoformat()
        changed_time["export_attestation"]["exported_at"] = changed_time[
            "exported_at"
        ]
        with self.assertRaises(ContractError):
            restored.restore_tenant(self.director, changed_time)

        with self.assertRaises(AuthorizationError):
            foreign_restored.restore_tenant(self.foreign_director, tenant_export)

        wrong_key_path = self.database_path.with_name("wrong-key-restore.sqlite3")
        wrong_key_host = _provision_platform_authority_host(
            wrong_key_path,
            deletion_ledger_path=self.deletion_ledger_path,
            authority_id="fictional_paperclip_approval_authority",
            approval_signing_key=os.urandom(32),
            initial_time=self.now,
            principals=self.provisioned_principals,
        )
        self.addCleanup(wrong_key_host.close)
        wrong_key_paperclip = wrong_key_host.client(self.director)
        wrong_key_restore = wrong_key_paperclip.artifacts()
        with self.assertRaises(ContractError):
            wrong_key_restore.restore_tenant(self.director, tenant_export)
        with self.assertRaises(KeyError):
            wrong_key_restore.get(self.director, "brief_backup")
        self.assertEqual(wrong_key_paperclip.audit_events(self.director), [])
        self.assertEqual(restored_paperclip.audit_events(self.director), [])

        self.assertEqual(restored.restore_tenant(self.director, tenant_export), 2)
        self.assertEqual(restored.get(self.director, "brief_backup"), brief)
        self.assertEqual(restored.get(self.director, "learning_backup"), learning)
        with self.assertRaises(ContractError):
            restored.restore_tenant(self.director, tenant_export)

    def test_artifact_offboarding_requires_current_export_and_is_durable(self) -> None:
        lantern_learning = self._learning("learning_lantern")
        ember_learning = self._learning(
            "learning_ember", brand_id=self.foreign_director.brand_id
        )
        self.artifacts.put(self.director, lantern_learning)
        self.foreign_artifacts.put(self.foreign_director, ember_learning)
        tenant_export = self.artifacts.export_tenant(self.director)

        with self.assertRaises(ContractError):
            self.artifacts.delete_tenant(self.director, "sha256:" + "0" * 64)
        self.assertEqual(
            self.artifacts.get(self.director, "learning_lantern"),
            lantern_learning,
        )

        with self.assertRaises(ContractError):
            self.artifacts.delete_tenant(
                self.director, tenant_export["export_checksum"]
            )
        queue_receipt = self.paperclip.work_queue().cancel_tenant(
            self.director,
            evidence_ref="evidence://offboarding/lantern-approved",
        )
        receipt = self.artifacts.delete_tenant(
            self.director, tenant_export["export_checksum"]
        )
        receipt_id = receipt["deletion_receipt_id"]
        self.assertEqual(receipt["record_count"], 1)
        self.assertEqual(
            receipt["queue_cancellation_receipt_id"],
            queue_receipt["queue_cancellation_receipt_id"],
        )
        self.assertNotIn("validated_correction", canonical_bytes(receipt).decode())
        artifact_audit = self.paperclip.audit_events(self.director)
        self.assertEqual(
            [event["event_type"] for event in artifact_audit],
            ["queue.tenant.cancelled", "artifact.tenant_deleted"],
        )
        self.assertNotIn("learning_lantern", canonical_bytes(artifact_audit).decode())
        with self.assertRaises(KeyError):
            self.artifacts.get(self.director, "learning_lantern")
        self.assertEqual(
            self.foreign_artifacts.get(self.foreign_director, "learning_ember"),
            ember_learning,
        )
        with self.assertRaises(AuthorizationError):
            self.artifacts.restore_tenant(self.director, tenant_export)
        with self.assertRaises(AuthorizationError):
            self.artifacts.put(self.director, lantern_learning)

        recovery_path = self.database_path.with_name(
            "post-deletion-recovery.sqlite3"
        )
        recovery_host = _provision_platform_authority_host(
            recovery_path,
            deletion_ledger_path=self.deletion_ledger_path,
            authority_id="fictional_paperclip_approval_authority",
            approval_signing_key=self.approval_signing_key,
            initial_time=self.now,
            principals=self.provisioned_principals,
        )
        self.addCleanup(recovery_host.close)
        recovery_paperclip = recovery_host.client(self.director)
        recovery_artifacts = recovery_paperclip.artifacts()
        with self.assertRaises(AuthorizationError):
            recovery_artifacts.restore_tenant(self.director, tenant_export)
        with self.assertRaises(KeyError):
            recovery_artifacts.get(self.director, "learning_lantern")
        self.assertEqual(recovery_paperclip.audit_events(self.director), [])

        self.platform_host.close()
        restarted = _provision_platform_authority_host(
            self.database_path,
            deletion_ledger_path=self.deletion_ledger_path,
            authority_id="fictional_paperclip_approval_authority",
            approval_signing_key=self.approval_signing_key,
            initial_time=self.now,
            principals=self.provisioned_principals,
        )
        self.addCleanup(restarted.close)
        restarted_artifacts = restarted.client(self.director).artifacts()
        self.assertEqual(
            restarted_artifacts.deletion_receipt(self.director, receipt_id), receipt
        )
        self.assertEqual(
            restarted.client(self.director)
            .work_queue()
            .cancellation_receipt(
                self.director, queue_receipt["queue_cancellation_receipt_id"]
            ),
            queue_receipt,
        )
        with self.assertRaises(KeyError):
            restarted_artifacts.get(self.director, "learning_lantern")
        self.assertEqual(
            restarted.client(self.foreign_director)
            .artifacts()
            .get(self.foreign_director, "learning_ember"),
            ember_learning,
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

        restarted = self.platform_host.client(self.director)
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

        rejected = self.approval_client.record_approval(
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

        self.approval_client.record_approval(
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
            self.unlisted_approval_client.record_approval(
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
            self.approval_client.record_approval(
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

        self.approval_client.record_approval(
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

        approval = self.approval_client.record_approval(
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
        self.assertEqual(
            approval["approval_attestation"]["authority_id"],
            "fictional_paperclip_approval_authority",
        )
        restarted = self.platform_host.client(self.director)
        closed = restarted.close_task(
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

    def test_listed_approver_sql_forgery_lacks_authority_attestation(self) -> None:
        self.paperclip.create_task(
            self.director, self._task("issue_impersonation", approval_required=True)
        )
        self.evidence.put(
            self.strategist,
            self._evidence("evidence_impersonation", issue_id="issue_impersonation"),
        )
        policy = self.paperclip.register_approver_policy(
            self.director, self._policy()
        )
        current = self._advance_to_in_progress("issue_impersonation")
        forged = finalize_record(
            {
                "schema_version": "1.0",
                "artifact_type": "paperclip_task_approval",
                "approval_id": "approval_impersonated_owner",
                "brand_id": "brand_lantern",
                "paperclip_issue_id": "issue_impersonation",
                "task_checksum": current["content_checksum"],
                "policy_id": policy["policy_id"],
                "policy_revision": policy["revision"],
                "policy_checksum": policy["content_checksum"],
                "decision": "APPROVED",
                "approver_id": self.approver.actor_id,
                "authority_role": self.approver.role_id,
                "decided_at": self.now.isoformat(),
                "expires_at": (self.now + timedelta(minutes=10)).isoformat(),
                "approval_attestation": {
                    "authority_id": "fictional_paperclip_approval_authority",
                    "algorithm": "HMAC-SHA256",
                    "signature": "0" * 64,
                },
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
                    forged["approval_id"],
                    "issue_impersonation",
                    current["content_checksum"],
                    canonical_bytes(forged).decode("utf-8"),
                    self.now.isoformat(),
                ),
            )
            connection.commit()
        finally:
            connection.close()

        before_task = self.paperclip.get_task(self.director, "issue_impersonation")
        before_audit = self.paperclip.audit_events(self.director)
        with self.assertRaises(ContractError):
            self.paperclip.close_task(
                self.director,
                "issue_impersonation",
                current["content_checksum"],
                evidence_refs=("evidence_impersonation",),
                approval_id=forged["approval_id"],
            )
        self.assertEqual(
            self.paperclip.get_task(self.director, "issue_impersonation"), before_task
        )
        self.assertEqual(self.paperclip.audit_events(self.director), before_audit)
        self.assertNotIn(
            "paperclip.approval.recorded",
            [event["event_type"] for event in before_audit],
        )

    def test_approval_attestation_binds_every_field_and_honest_key(self) -> None:
        self.paperclip.create_task(
            self.director,
            self._task("issue_attestation_binding", approval_required=True),
        )
        self.evidence.put(
            self.strategist,
            self._evidence(
                "evidence_attestation_binding",
                issue_id="issue_attestation_binding",
            ),
        )
        policy = self.paperclip.register_approver_policy(
            self.director, self._policy()
        )
        current = self._advance_to_in_progress("issue_attestation_binding")
        legitimate = self.approval_client.record_approval(
            self.approver,
            approval_id="approval_attestation_binding",
            issue_id="issue_attestation_binding",
            expected_task_checksum=current["content_checksum"],
            policy_id=policy["policy_id"],
            policy_revision=policy["revision"],
            policy_checksum=policy["content_checksum"],
            decision="APPROVED",
            decided_at=self.now.isoformat(),
            expires_at=(self.now + timedelta(minutes=10)).isoformat(),
        )
        before_task = self.paperclip.get_task(
            self.director, "issue_attestation_binding"
        )
        before_audit = self.paperclip.audit_events(self.director)

        replacements = {
            "approval_id": "approval_attestation_changed",
            "brand_id": "brand_ember",
            "paperclip_issue_id": "issue_other",
            "task_checksum": "sha256:" + "1" * 64,
            "policy_id": "other_policy",
            "policy_revision": 2,
            "policy_checksum": "sha256:" + "2" * 64,
            "decision": "REJECTED",
            "approver_id": "another_approver",
            "authority_role": "agency-director",
            "decided_at": (self.now - timedelta(minutes=1)).isoformat(),
            "expires_at": (self.now + timedelta(minutes=11)).isoformat(),
        }
        forged_records: list[dict] = []
        for field, replacement in replacements.items():
            forged = copy.deepcopy(legitimate)
            forged.pop("content_checksum")
            forged[field] = replacement
            forged_records.append(finalize_record(forged))

        without_attestation = copy.deepcopy(legitimate)
        without_attestation.pop("content_checksum")
        without_attestation.pop("approval_attestation")
        forged_records.append(finalize_record(without_attestation))

        malformed_attestation = copy.deepcopy(legitimate)
        malformed_attestation.pop("content_checksum")
        malformed_attestation["approval_attestation"] = "not-an-attestation"
        forged_records.append(finalize_record(malformed_attestation))

        for attestation_field, replacement in (
            ("authority_id", "attacker_authority"),
            ("algorithm", "HMAC-SHA512"),
            ("signature", "f" * 64),
        ):
            forged = copy.deepcopy(legitimate)
            forged.pop("content_checksum")
            forged["approval_attestation"][attestation_field] = replacement
            forged_records.append(finalize_record(forged))

        attacker_body = copy.deepcopy(legitimate)
        attacker_body.pop("content_checksum")
        attacker_body.pop("approval_attestation")
        attacker_signature = hmac.new(
            os.urandom(32),
            b"agency-os.paperclip-task-approval.v1\x00"
            + canonical_bytes(attacker_body),
            hashlib.sha256,
        ).hexdigest()
        attacker_body["approval_attestation"] = {
            "authority_id": "fictional_paperclip_approval_authority",
            "algorithm": "HMAC-SHA256",
            "signature": attacker_signature,
        }
        forged_records.append(finalize_record(attacker_body))

        for forged in forged_records:
            connection = sqlite3.connect(self.database_path)
            try:
                connection.execute(
                    """
                    UPDATE paperclip_approvals SET record_json = ?
                    WHERE brand_id = ? AND approval_id = ?
                    """,
                    (
                        canonical_bytes(forged).decode("utf-8"),
                        "brand_lantern",
                        legitimate["approval_id"],
                    ),
                )
                connection.commit()
            finally:
                connection.close()
            with self.assertRaises(ContractError):
                self.paperclip.close_task(
                    self.director,
                    "issue_attestation_binding",
                    current["content_checksum"],
                    evidence_refs=("evidence_attestation_binding",),
                    approval_id=legitimate["approval_id"],
                )
            self.assertEqual(
                self.paperclip.get_task(self.director, "issue_attestation_binding"),
                before_task,
            )
            self.assertEqual(self.paperclip.audit_events(self.director), before_audit)

        self.assertNotIn(self.approval_signing_key, self.database_path.read_bytes())

    def test_worker_cannot_self_provision_or_replace_approval_authority(self) -> None:
        self.paperclip.create_task(
            self.director,
            self._task("issue_authority_boundary", approval_required=True),
        )
        self.evidence.put(
            self.strategist,
            self._evidence(
                "evidence_authority_boundary",
                issue_id="issue_authority_boundary",
            ),
        )
        policy = self.paperclip.register_approver_policy(
            self.director, self._policy()
        )
        current = self._advance_to_in_progress("issue_authority_boundary")
        before_task = self.paperclip.get_task(
            self.director, "issue_authority_boundary"
        )
        before_audit = self.paperclip.audit_events(self.director)

        self.assertIsInstance(self.paperclip, PlatformAuthorityClient)
        self.assertEqual(
            PlatformAuthorityClient.__slots__,
            ("_socket_path", "_principal", "_client_token"),
        )
        self.assertEqual(TenantWorkQueueClient.__slots__, ("_authority",))
        for forbidden_export in (
            "FictionalApprovalAuthority",
            "FictionalRecoveryAuthority",
            "FictionalPaperclipAdapter",
            "SQLiteTenantEvidenceStore",
            "SQLiteTenantArtifactStore",
            "SQLiteArtifactDeletionLedger",
            "AuthorityWorkQueue",
            "provision_platform_authority_host",
        ):
            self.assertFalse(hasattr(agency_os, forbidden_export))

        with self.assertRaises(ContractError):
            _FictionalApprovalAuthority(
                authority_id="fictional_paperclip_approval_authority",
                signing_key=os.urandom(32),
                _construction_token=object(),
            )
        with self.assertRaises(ContractError):
            _FictionalRecoveryAuthority(
                authority_id="fictional_paperclip_approval_authority",
                signing_key=os.urandom(32),
                _construction_token=object(),
            )
        with self.assertRaises(PlatformAdapterError):
            _AuthorityPaperclipAdapter(
                self.database_path,
                approval_authority=object(),
                _construction_token=object(),
            )
        with self.assertRaises(ArtifactStoreError):
            _SQLiteArtifactDeletionLedger(
                self.database_path.with_name("attacker-deletions.sqlite3"),
                authority_id="fictional_paperclip_approval_authority",
                timeout_seconds=5.0,
                allow_create=True,
                _construction_token=object(),
            )

        forged_identity = self.platform_host.client(self.director)
        object.__setattr__(forged_identity, "_principal", self.approver)
        with self.assertRaises(AuthorizationError):
            forged_identity.record_approval(
                self.approver,
                approval_id="attacker_self_signed",
                issue_id="issue_authority_boundary",
                expected_task_checksum=current["content_checksum"],
                policy_id=policy["policy_id"],
                policy_revision=policy["revision"],
                policy_checksum=policy["content_checksum"],
                decision="APPROVED",
                decided_at=self.now.isoformat(),
                expires_at=(self.now + timedelta(minutes=10)).isoformat(),
            )

        with self.assertRaises(AuthorizationError):
            self.platform_host.client(
                Principal("attacker", "human-approver", "brand_lantern")
            )
        invented_client = PlatformAuthorityClient(
            object.__getattribute__(self.paperclip, "_socket_path"),
            self.approver,
            "0" * 64,
        )
        with self.assertRaises(AuthorizationError):
            invented_client.record_approval(
                self.approver,
                approval_id="attacker_invented_client",
                issue_id="issue_authority_boundary",
                expected_task_checksum=current["content_checksum"],
                policy_id=policy["policy_id"],
                policy_revision=policy["revision"],
                policy_checksum=policy["content_checksum"],
                decision="APPROVED",
                decided_at=self.now.isoformat(),
                expires_at=(self.now + timedelta(minutes=10)).isoformat(),
            )

        forged_token = self.platform_host.client(self.director)
        object.__setattr__(forged_token, "_client_token", "0" * 64)
        with self.assertRaises(AuthorizationError):
            forged_token.get_task(self.director, "issue_authority_boundary")

        redirected = self.platform_host.client(self.director)
        with self.assertRaises(AttributeError):
            object.__setattr__(redirected, "_approval_authority", object())
        with self.assertRaises(AttributeError):
            object.__setattr__(redirected, "_deletion_ledger", object())
        object.__setattr__(redirected, "_socket_path", str(self.database_path))
        with self.assertRaises(PlatformAuthorityUnavailable):
            redirected.close_task(
                self.director,
                "issue_authority_boundary",
                current["content_checksum"],
                evidence_refs=("evidence_authority_boundary",),
                approval_id="attacker_self_signed",
            )
        self.assertEqual(
            self.paperclip.get_task(self.director, "issue_authority_boundary"),
            before_task,
        )
        self.assertEqual(self.paperclip.audit_events(self.director), before_audit)

    def test_unavailable_platform_authority_fails_closed(self) -> None:
        self.paperclip.create_task(
            self.director, self._task("issue_unprovisioned", approval_required=True)
        )
        policy = self.paperclip.register_approver_policy(
            self.director, self._policy()
        )
        current = self._advance_to_in_progress("issue_unprovisioned")
        before_task = self.paperclip.get_task(self.director, "issue_unprovisioned")
        before_audit = self.paperclip.audit_events(self.director)
        unavailable_client = self.approval_client
        self.platform_host.close()
        with self.assertRaises(PlatformAuthorityUnavailable):
            unavailable_client.record_approval(
                self.approver,
                approval_id="approval_unprovisioned",
                issue_id="issue_unprovisioned",
                expected_task_checksum=current["content_checksum"],
                policy_id=policy["policy_id"],
                policy_revision=policy["revision"],
                policy_checksum=policy["content_checksum"],
                decision="APPROVED",
                decided_at=self.now.isoformat(),
                expires_at=(self.now + timedelta(minutes=10)).isoformat(),
            )
        self.platform_host = _provision_platform_authority_host(
            self.database_path,
            deletion_ledger_path=self.deletion_ledger_path,
            authority_id="fictional_paperclip_approval_authority",
            approval_signing_key=self.approval_signing_key,
            initial_time=self.now,
            principals=self.provisioned_principals,
        )
        self.addCleanup(self.platform_host.close)
        self.paperclip = self.platform_host.client(self.director)
        self.assertEqual(
            self.paperclip.get_task(self.director, "issue_unprovisioned"), before_task
        )
        self.assertEqual(self.paperclip.audit_events(self.director), before_audit)

    def test_task_tenant_and_optimistic_concurrency_boundaries_fail_closed(self) -> None:
        task = self._task("issue_asset")
        self.paperclip.create_task(self.director, task)
        with self.assertRaises(KeyError):
            self.foreign_paperclip.get_task(self.foreign_director, "issue_asset")
        with self.assertRaises(AuthorizationError):
            self.foreign_paperclip.create_task(self.foreign_director, task)

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
        restarted = self.platform_host.client(self.director)
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
        paperclip = self.paperclip
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
        self.platform_host.set_time(authority_time[0])

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

        restarted = self.platform_host.client(self.director)
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
            self.foreign_paperclip.get_buzz_decision(
                self.foreign_director, "decision_asset"
            )

    def test_evidence_is_persistent_immutable_and_tenant_isolated(self) -> None:
        self.paperclip.create_task(self.director, self._task("issue_asset"))
        evidence = self._evidence()
        self.evidence.put(self.strategist, evidence)
        restarted = self.platform_host.client(self.strategist).evidence()
        self.assertEqual(restarted.get(self.strategist, "evidence_primary"), evidence)
        self.assertEqual(
            restarted.list_for_issue(self.strategist, "issue_asset"), [evidence]
        )
        with self.assertRaises(KeyError):
            self.foreign_paperclip.evidence().get(
                self.foreign_director, "evidence_primary"
            )
        with self.assertRaises(AuthorizationError):
            self.foreign_paperclip.evidence().put(self.foreign_director, evidence)

        changed = copy.deepcopy(evidence)
        changed["claim"] = "A changed claim."
        changed = finalize_record(changed)
        with self.assertRaises(ContractError):
            restarted.put(self.strategist, changed)

    def test_evidence_wrong_role_and_actor_are_denied(self) -> None:
        evidence = self._evidence()
        with self.assertRaises(ContractError):
            self.evidence.put(self.strategist, evidence)
        with self.assertRaises(AuthorizationError):
            self.publisher_paperclip.evidence().put(self.publisher, evidence)
        forged = copy.deepcopy(evidence)
        forged["created_by"] = "another_actor"
        forged = finalize_record(forged)
        with self.assertRaises(AuthorizationError):
            self.evidence.put(self.strategist, forged)

    def test_internal_queue_lease_retry_dead_letter_survives_restart(self) -> None:
        self.paperclip.create_task(self.director, self._task("issue_queue_retry"))
        task = self._advance_to_in_progress("issue_queue_retry")
        item = self._work_item("work_retry", task, max_attempts=2)
        director_queue = self.paperclip.work_queue()
        strategist_queue = self.strategist_paperclip.work_queue()
        self.assertEqual(director_queue.enqueue(self.director, item), "work_retry")

        first = strategist_queue.lease_next(self.strategist, 10)
        self.assertEqual(first["lease"]["attempt_count"], 1)
        self.assertIsNone(
            self.platform_host.client(self.strategist).work_queue().lease_next(
                self.strategist, 10
            )
        )
        with self.assertRaises(AuthorizationError):
            self.publisher_paperclip.work_queue().complete(
                self.publisher,
                "work_retry",
                first["lease"]["lease_token"],
            )
        renewed = strategist_queue.heartbeat(
            self.strategist,
            "work_retry",
            first["lease"]["lease_token"],
            10,
        )
        self.assertEqual(renewed["lease_owner"], self.strategist.actor_id)

        self.platform_host.close()
        self.now += timedelta(seconds=11)
        restarted = _provision_platform_authority_host(
            self.database_path,
            deletion_ledger_path=self.deletion_ledger_path,
            authority_id="fictional_paperclip_approval_authority",
            approval_signing_key=self.approval_signing_key,
            initial_time=self.now,
            principals=self.provisioned_principals,
        )
        self.addCleanup(restarted.close)
        second = restarted.client(self.strategist).work_queue().lease_next(
            self.strategist, 10
        )
        self.assertEqual(second["lease"]["attempt_count"], 2)
        dead_letter = restarted.client(self.strategist).work_queue().fail(
            self.strategist,
            "work_retry",
            second["lease"]["lease_token"],
            error_class="INTERNAL_PERMANENT",
            retryable=False,
            external_result="NOT_APPLICABLE",
        )
        self.assertEqual(dead_letter["state"], "DEAD_LETTER")
        self.assertEqual(
            dead_letter["error_classes"],
            ["LEASE_EXPIRED", "INTERNAL_PERMANENT"],
        )
        director_queue = restarted.client(self.director).work_queue()
        dispositioned = director_queue.record_dead_letter_disposition(
            self.director,
            "work_retry",
            evidence_ref="evidence://human-review/retry",
            disposition="Do not reopen; create a new Paperclip task if needed.",
        )
        self.assertEqual(
            dispositioned["dispositions"][-1]["outcome"], "DEAD_LETTER"
        )
        with self.assertRaises(ContractError):
            director_queue.record_dead_letter_disposition(
                self.director,
                "work_retry",
                evidence_ref="evidence://human-review/changed",
                disposition="Attempt to change immutable disposition.",
            )
        self.assertIsNone(
            restarted.client(self.strategist).work_queue().lease_next(
                self.strategist, 10
            )
        )

    def test_external_unknown_requires_reconciliation_before_retry(self) -> None:
        self.paperclip.create_task(self.director, self._task("issue_queue_external"))
        task = self._advance_to_in_progress("issue_queue_external")
        item = self._work_item(
            "work_external",
            task,
            work_kind="external_write",
            worker_role="publishing-operator",
            max_attempts=2,
        )
        director_queue = self.paperclip.work_queue()
        publisher_queue = self.publisher_paperclip.work_queue()
        director_queue.enqueue(self.director, item)
        first = publisher_queue.lease_next(self.publisher, 10)
        unknown = publisher_queue.fail(
            self.publisher,
            "work_external",
            first["lease"]["lease_token"],
            error_class="EXTERNAL_TIMEOUT",
            retryable=True,
            external_result="UNKNOWN",
        )
        self.assertEqual(unknown["state"], "RECONCILIATION_REQUIRED")
        self.assertIsNone(publisher_queue.lease_next(self.publisher, 10))
        with self.assertRaises(AuthorizationError):
            publisher_queue.reconcile(
                self.publisher,
                "work_external",
                outcome="CONFIRMED_NO_WRITE",
                evidence_ref="evidence://destination/check",
                disposition="Retry safely.",
            )
        reconciled = director_queue.reconcile(
            self.director,
            "work_external",
            outcome="CONFIRMED_NO_WRITE",
            evidence_ref="evidence://destination/check",
            disposition="Destination confirms no write; retry once.",
        )
        self.assertEqual(reconciled["state"], "READY")
        second = publisher_queue.lease_next(self.publisher, 10)
        completed = publisher_queue.complete(
            self.publisher,
            "work_external",
            second["lease"]["lease_token"],
        )
        self.assertEqual(completed["state"], "COMPLETED")
        self.assertEqual(len(completed["dispositions"]), 1)
        current_task = self.paperclip.get_task(self.director, "issue_queue_external")
        self.assertEqual(current_task, task)

        expired_item = self._work_item(
            "work_external_expired",
            task,
            work_kind="external_write",
            worker_role="publishing-operator",
            max_attempts=1,
        )
        director_queue.enqueue(self.director, expired_item)
        publisher_queue.lease_next(self.publisher, 5)
        self.now += timedelta(seconds=6)
        self.platform_host.set_time(self.now)
        self.assertIsNone(publisher_queue.lease_next(self.publisher, 5))
        self.assertEqual(
            director_queue.get(self.director, "work_external_expired")["state"],
            "RECONCILIATION_REQUIRED",
        )
        exhausted = director_queue.reconcile(
            self.director,
            "work_external_expired",
            outcome="CONFIRMED_NO_WRITE",
            evidence_ref="evidence://destination/expired-check",
            disposition="No write occurred, but the attempt limit is exhausted.",
        )
        self.assertEqual(exhausted["state"], "DEAD_LETTER")
        dispositioned = director_queue.record_dead_letter_disposition(
            self.director,
            "work_external_expired",
            evidence_ref="evidence://human-review/expired",
            disposition="Do not reopen this exhausted external work item.",
        )
        self.assertEqual(
            [entry["outcome"] for entry in dispositioned["dispositions"]],
            ["CONFIRMED_NO_WRITE", "DEAD_LETTER"],
        )

    def test_post_lease_task_drift_blocks_internal_and_external_completion(
        self,
    ) -> None:
        director_queue = self.paperclip.work_queue()

        self.paperclip.create_task(self.director, self._task("issue_internal_drift"))
        internal_planned = self.paperclip.get_task(
            self.director, "issue_internal_drift"
        )
        internal_ready = self.paperclip.set_status(
            self.director,
            "issue_internal_drift",
            internal_planned["content_checksum"],
            "ready",
        )
        director_queue.enqueue(
            self.director,
            self._work_item("work_internal_drift", internal_ready),
        )
        internal_lease = self.strategist_paperclip.work_queue().lease_next(
            self.strategist, 10
        )
        self.paperclip.set_status(
            self.director,
            "issue_internal_drift",
            internal_ready["content_checksum"],
            "in_progress",
        )
        internal_result = self.strategist_paperclip.work_queue().complete(
            self.strategist,
            "work_internal_drift",
            internal_lease["lease"]["lease_token"],
        )
        self.assertEqual(internal_result["state"], "DEAD_LETTER")
        self.assertEqual(internal_result["error_classes"], ["TASK_DRIFT"])

        self.paperclip.create_task(self.director, self._task("issue_external_drift"))
        external_planned = self.paperclip.get_task(
            self.director, "issue_external_drift"
        )
        external_ready = self.paperclip.set_status(
            self.director,
            "issue_external_drift",
            external_planned["content_checksum"],
            "ready",
        )
        director_queue.enqueue(
            self.director,
            self._work_item(
                "work_external_drift",
                external_ready,
                work_kind="external_write",
                worker_role="publishing-operator",
            ),
        )
        external_lease = self.publisher_paperclip.work_queue().lease_next(
            self.publisher, 10
        )
        self.paperclip.set_status(
            self.director,
            "issue_external_drift",
            external_ready["content_checksum"],
            "in_progress",
        )
        external_result = self.publisher_paperclip.work_queue().complete(
            self.publisher,
            "work_external_drift",
            external_lease["lease"]["lease_token"],
        )
        self.assertEqual(external_result["state"], "RECONCILIATION_REQUIRED")
        self.assertEqual(external_result["error_classes"], ["TASK_DRIFT"])
        self.assertIsNone(
            self.publisher_paperclip.work_queue().lease_next(self.publisher, 10)
        )

        item_events = {
            subject_id: [
                event["event_type"]
                for event in self.paperclip.audit_events(self.director)
                if event["subject_id"] == subject_id
            ]
            for subject_id in ("work_internal_drift", "work_external_drift")
        }
        self.assertEqual(
            item_events["work_internal_drift"],
            [
                "queue.item.enqueued",
                "queue.item.leased",
                "queue.item.dead_letter",
            ],
        )
        self.assertEqual(
            item_events["work_external_drift"],
            [
                "queue.item.enqueued",
                "queue.item.leased",
                "queue.item.task_drift.reconciliation_required",
            ],
        )
        self.assertNotIn(
            "queue.item.completed",
            [
                event_type
                for event_types in item_events.values()
                for event_type in event_types
            ],
        )

    def test_queue_offboarding_is_evidence_bound_durable_and_fail_closed(
        self,
    ) -> None:
        self.paperclip.create_task(self.director, self._task("issue_queue_offboard"))
        task = self._advance_to_in_progress("issue_queue_offboard")
        director_queue = self.paperclip.work_queue()
        strategist_queue = self.strategist_paperclip.work_queue()
        publisher_queue = self.publisher_paperclip.work_queue()

        completed_item = self._work_item("work_offboard_completed", task)
        director_queue.enqueue(self.director, completed_item)
        completed_lease = strategist_queue.lease_next(self.strategist, 10)
        completed = strategist_queue.complete(
            self.strategist,
            "work_offboard_completed",
            completed_lease["lease"]["lease_token"],
        )
        self.assertEqual(completed["state"], "COMPLETED")

        leased_item = self._work_item("work_offboard_leased", task)
        director_queue.enqueue(self.director, leased_item)
        leased = strategist_queue.lease_next(self.strategist, 10)
        with self.assertRaises(ContractError):
            strategist_queue.fail(
                self.strategist,
                "work_offboard_leased",
                leased["lease"]["lease_token"],
                error_class="TENANT_OFFBOARDED",
                retryable=False,
                external_result="NOT_APPLICABLE",
            )
        ready_item = self._work_item("work_offboard_ready", task)
        director_queue.enqueue(self.director, ready_item)

        external_item = self._work_item(
            "work_offboard_external",
            task,
            work_kind="external_write",
            worker_role="publishing-operator",
        )
        director_queue.enqueue(self.director, external_item)
        external_lease = publisher_queue.lease_next(self.publisher, 10)

        ember_planned = self._task(
            "issue_queue_offboard_ember",
            brand_id=self.foreign_director.brand_id,
            created_by=self.foreign_director.actor_id,
        )
        self.foreign_paperclip.create_task(self.foreign_director, ember_planned)
        ember_ready = self.foreign_paperclip.set_status(
            self.foreign_director,
            "issue_queue_offboard_ember",
            ember_planned["content_checksum"],
            "ready",
        )
        foreign_queue = self.foreign_paperclip.work_queue()
        foreign_queue.enqueue(
            self.foreign_director,
            self._work_item(
                "work_offboard_ember",
                ember_ready,
                brand_id=self.foreign_director.brand_id,
                created_by=self.foreign_director.actor_id,
            ),
        )

        with self.assertRaises(AuthorizationError):
            publisher_queue.cancel_tenant(
                self.publisher,
                evidence_ref="evidence://offboarding/not-director",
            )
        with self.assertRaises(ContractError):
            director_queue.cancel_tenant(
                self.director,
                evidence_ref="evidence://offboarding/approved",
            )
        self.assertEqual(
            director_queue.get(self.director, "work_offboard_leased")["state"],
            "LEASED",
        )
        unknown = publisher_queue.fail(
            self.publisher,
            "work_offboard_external",
            external_lease["lease"]["lease_token"],
            error_class="EXTERNAL_TIMEOUT",
            retryable=True,
            external_result="UNKNOWN",
        )
        self.assertEqual(unknown["state"], "RECONCILIATION_REQUIRED")
        with self.assertRaises(ContractError):
            director_queue.cancel_tenant(
                self.director,
                evidence_ref="evidence://offboarding/approved",
            )
        reconciled = director_queue.reconcile(
            self.director,
            "work_offboard_external",
            outcome="CONFIRMED_NO_WRITE",
            evidence_ref="evidence://destination/no-write",
            disposition="Destination confirms no write before offboarding.",
        )
        self.assertEqual(reconciled["state"], "READY")

        before_task = self.paperclip.get_task(self.director, "issue_queue_offboard")
        receipt = director_queue.cancel_tenant(
            self.director,
            evidence_ref="evidence://offboarding/approved",
        )
        receipt_id = receipt["queue_cancellation_receipt_id"]
        self.assertEqual(receipt["work_item_count"], 4)
        self.assertEqual(receipt["cancelled_item_count"], 3)
        self.assertEqual(receipt["terminal_item_count"], 1)
        receipt_bytes = canonical_bytes(receipt).decode()
        self.assertNotIn("work_offboard", receipt_bytes)
        self.assertNotIn("fictional_operation", receipt_bytes)
        self.assertEqual(
            director_queue.cancel_tenant(
                self.director,
                evidence_ref="evidence://offboarding/approved",
            ),
            receipt,
        )
        with self.assertRaises(ContractError):
            director_queue.cancel_tenant(
                self.director,
                evidence_ref="evidence://offboarding/replacement",
            )

        for work_item_id in (
            "work_offboard_leased",
            "work_offboard_ready",
            "work_offboard_external",
        ):
            view = director_queue.get(self.director, work_item_id)
            self.assertEqual(view["state"], "DEAD_LETTER")
            self.assertIn("TENANT_OFFBOARDED", view["error_classes"])
            self.assertIsNone(view["lease_owner"])
            self.assertIsNone(view["lease_expires_at"])
        external_view = director_queue.get(self.director, "work_offboard_external")
        self.assertEqual(
            [entry["outcome"] for entry in external_view["dispositions"]],
            ["CONFIRMED_NO_WRITE"],
        )
        self.assertEqual(
            director_queue.get(self.director, "work_offboard_completed")["state"],
            "COMPLETED",
        )
        self.assertEqual(len(director_queue.dead_letters(self.director)), 3)
        with self.assertRaises(AuthorizationError):
            strategist_queue.get(self.strategist, "work_offboard_leased")
        with self.assertRaises(AuthorizationError):
            publisher_queue.cancellation_receipt(self.publisher, receipt_id)
        with self.assertRaises(ContractError):
            strategist_queue.heartbeat(
                self.strategist,
                "work_offboard_leased",
                leased["lease"]["lease_token"],
                10,
            )
        with self.assertRaises(ContractError):
            strategist_queue.lease_next(self.strategist, 10)
        with self.assertRaises(ContractError):
            director_queue.enqueue(
                self.director,
                self._work_item("work_offboard_new", task),
            )
        with self.assertRaises(ContractError):
            director_queue.reconcile(
                self.director,
                "work_offboard_external",
                outcome="CONFIRMED_COMPLETED",
                evidence_ref="evidence://destination/late",
                disposition="Late mutation must fail.",
            )
        with self.assertRaises(KeyError):
            foreign_queue.cancellation_receipt(self.foreign_director, receipt_id)
        self.assertEqual(
            foreign_queue.get(self.foreign_director, "work_offboard_ember")["state"],
            "READY",
        )
        self.assertEqual(
            self.paperclip.get_task(self.director, "issue_queue_offboard"), before_task
        )

        event_types = [
            event["event_type"] for event in self.paperclip.audit_events(self.director)
        ]
        self.assertEqual(event_types.count("queue.item.offboarding_dead_letter"), 3)
        self.assertEqual(event_types.count("queue.tenant.cancelled"), 1)

        self.platform_host.close()
        restarted = _provision_platform_authority_host(
            self.database_path,
            deletion_ledger_path=self.deletion_ledger_path,
            authority_id="fictional_paperclip_approval_authority",
            approval_signing_key=self.approval_signing_key,
            initial_time=self.now,
            principals=self.provisioned_principals,
        )
        self.addCleanup(restarted.close)
        restarted_director_queue = restarted.client(self.director).work_queue()
        self.assertEqual(
            restarted_director_queue.cancellation_receipt(self.director, receipt_id),
            receipt,
        )
        self.assertEqual(
            restarted_director_queue.get(self.director, "work_offboard_leased")[
                "state"
            ],
            "DEAD_LETTER",
        )
        with self.assertRaises(ContractError):
            restarted.client(self.strategist).work_queue().lease_next(
                self.strategist, 10
            )

    def test_full_local_tenant_offboarding_is_coordinated_and_durable(self) -> None:
        self.paperclip.create_task(
            self.director, self._task("issue_authority_offboard")
        )
        task = self.paperclip.set_status(
            self.director,
            "issue_authority_offboard",
            self.paperclip.get_task(
                self.director, "issue_authority_offboard"
            )["content_checksum"],
            "ready",
        )
        self.evidence.put(
            self.strategist,
            self._evidence(
                "evidence_authority_offboard",
                issue_id="issue_authority_offboard",
            ),
        )
        lantern_learning = self._learning("learning_authority_offboard")
        self.artifacts.put(self.director, lantern_learning)
        context = make_buzz_context_packet(
            context_id="context_authority_offboard",
            brand_id=self.director.brand_id,
            campaign_id="campaign_launch",
            paperclip_issue_id="issue_authority_offboard",
            purpose="Prepare fictional local tenant offboarding.",
            decision_needed="Confirm the bounded deletion manifest.",
            participants=(self.director.actor_id, self.strategist.actor_id),
            source_artifact_ids=("evidence_authority_offboard",),
            constraints=("No production activation.",),
            deadline=(self.now + timedelta(hours=1)).isoformat(),
            exit_condition="The local offboarding receipt is durable.",
            created_by=self.director.actor_id,
            created_at=self.now.isoformat(),
        )
        self.paperclip.record_buzz_context(self.director, context)
        queue = self.paperclip.work_queue()
        queue.enqueue(
            self.director,
            self._work_item("work_authority_offboard", task),
        )

        ember_task = self._task(
            "issue_authority_ember",
            brand_id=self.foreign_director.brand_id,
            created_by=self.foreign_director.actor_id,
        )
        self.foreign_paperclip.create_task(self.foreign_director, ember_task)
        ember_learning = self._learning(
            "learning_authority_ember",
            brand_id=self.foreign_director.brand_id,
        )
        self.foreign_artifacts.put(self.foreign_director, ember_learning)

        with self.assertRaises(ContractError):
            self.paperclip.prepare_tenant_offboarding(self.director)
        queue_receipt = queue.cancel_tenant(
            self.director,
            evidence_ref="evidence://offboarding/authority-approved",
        )
        stale_manifest = self.paperclip.prepare_tenant_offboarding(self.director)
        self.evidence.put(
            self.strategist,
            self._evidence(
                "evidence_after_manifest",
                issue_id="issue_authority_offboard",
            ),
        )
        with self.assertRaises(ContractError):
            self.paperclip.offboard_tenant(
                self.director,
                expected_authority_manifest_checksum=stale_manifest[
                    "authority_manifest_checksum"
                ],
                evidence_ref="evidence://offboarding/full-local",
            )
        self.assertEqual(
            self.paperclip.get_task(self.director, "issue_authority_offboard"),
            task,
        )
        self.assertEqual(
            self.artifacts.get(self.director, "learning_authority_offboard"),
            lantern_learning,
        )

        manifest = self.paperclip.prepare_tenant_offboarding(self.director)
        self.assertEqual(
            manifest["queue_cancellation_receipt_id"],
            queue_receipt["queue_cancellation_receipt_id"],
        )
        self.assertGreater(manifest["tables"]["platform_audit"]["row_count"], 0)
        with self.assertRaises(AuthorizationError):
            self.strategist_paperclip.offboard_tenant(
                self.strategist,
                expected_authority_manifest_checksum=manifest[
                    "authority_manifest_checksum"
                ],
                evidence_ref="evidence://offboarding/full-local",
            )

        receipt = self.paperclip.offboard_tenant(
            self.director,
            expected_authority_manifest_checksum=manifest[
                "authority_manifest_checksum"
            ],
            evidence_ref="evidence://offboarding/full-local",
        )
        receipt_id = receipt["tenant_offboarding_receipt_id"]
        self.assertEqual(
            receipt["queue_cancellation_receipt_id"],
            queue_receipt["queue_cancellation_receipt_id"],
        )
        receipt_text = canonical_bytes(receipt).decode()
        for deleted_identifier in (
            "issue_authority_offboard",
            "evidence_authority_offboard",
            "evidence_after_manifest",
            "context_authority_offboard",
            "learning_authority_offboard",
            "work_authority_offboard",
        ):
            self.assertNotIn(deleted_identifier, receipt_text)
        self.assertEqual(
            self.paperclip.offboard_tenant(
                self.director,
                expected_authority_manifest_checksum=manifest[
                    "authority_manifest_checksum"
                ],
                evidence_ref="evidence://offboarding/full-local",
            ),
            receipt,
        )
        with self.assertRaises(ContractError):
            self.paperclip.offboard_tenant(
                self.director,
                expected_authority_manifest_checksum=manifest[
                    "authority_manifest_checksum"
                ],
                evidence_ref="evidence://offboarding/replacement",
            )

        reviewer_client = self.platform_host.client(self.reviewer)
        self.assertEqual(
            reviewer_client.tenant_offboarding_receipt(
                self.reviewer, receipt_id
            ),
            receipt,
        )
        self.assertEqual(
            self.artifacts.deletion_receipt(
                self.director, receipt["artifact_deletion_receipt_id"]
            )["queue_cancellation_receipt_id"],
            queue_receipt["queue_cancellation_receipt_id"],
        )
        self.assertEqual(
            queue.cancellation_receipt(
                self.director, queue_receipt["queue_cancellation_receipt_id"]
            ),
            queue_receipt,
        )
        with self.assertRaises(AuthorizationError):
            self.strategist_paperclip.tenant_offboarding_receipt(
                self.strategist, receipt_id
            )
        for denied_call in (
            lambda: self.paperclip.get_task(
                self.director, "issue_authority_offboard"
            ),
            lambda: self.evidence.get(
                self.strategist, "evidence_authority_offboard"
            ),
            lambda: self.artifacts.get(
                self.director, "learning_authority_offboard"
            ),
            lambda: self.paperclip.get_buzz_context(
                self.director, "context_authority_offboard"
            ),
            lambda: self.paperclip.audit_events(self.director),
            lambda: self.paperclip.prepare_tenant_offboarding(self.director),
        ):
            with self.assertRaises(AuthorizationError):
                denied_call()

        self.assertEqual(
            self.foreign_paperclip.get_task(
                self.foreign_director, "issue_authority_ember"
            ),
            ember_task,
        )
        self.assertEqual(
            self.foreign_artifacts.get(
                self.foreign_director, "learning_authority_ember"
            ),
            ember_learning,
        )
        with sqlite3.connect(self.database_path) as connection:
            for table in (
                "paperclip_task_versions",
                "paperclip_approver_policies",
                "paperclip_approvals",
                "paperclip_buzz_contexts",
                "paperclip_buzz_decisions",
                "tenant_evidence",
                "tenant_artifacts",
                "platform_work_queue",
                "platform_audit",
            ):
                count = connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE brand_id = ?",
                    (self.director.brand_id,),
                ).fetchone()[0]
                self.assertEqual(count, 0, table)
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM tenant_queue_cancellations
                    WHERE brand_id = ?
                    """,
                    (self.director.brand_id,),
                ).fetchone()[0],
                1,
            )

        self.platform_host.close()
        restarted = _provision_platform_authority_host(
            self.database_path,
            deletion_ledger_path=self.deletion_ledger_path,
            authority_id="fictional_paperclip_approval_authority",
            approval_signing_key=self.approval_signing_key,
            initial_time=self.now,
            principals=self.provisioned_principals,
        )
        self.addCleanup(restarted.close)
        restarted_director = restarted.client(self.director)
        self.assertEqual(
            restarted_director.tenant_offboarding_receipt(
                self.director, receipt_id
            ),
            receipt,
        )
        with self.assertRaises(AuthorizationError):
            restarted_director.get_task(
                self.director, "issue_authority_offboard"
            )
        self.assertEqual(
            restarted.client(self.foreign_director).get_task(
                self.foreign_director, "issue_authority_ember"
            ),
            ember_task,
        )

        recovery_path = self.database_path.with_name(
            "authority-offboarding-recovery.sqlite3"
        )
        recovery = _provision_platform_authority_host(
            recovery_path,
            deletion_ledger_path=self.deletion_ledger_path,
            authority_id="fictional_paperclip_approval_authority",
            approval_signing_key=self.approval_signing_key,
            initial_time=self.now,
            principals=self.provisioned_principals,
        )
        self.addCleanup(recovery.close)
        recovery_director = recovery.client(self.director)
        self.assertEqual(
            recovery_director.tenant_offboarding_receipt(
                self.director, receipt_id
            ),
            receipt,
        )
        with self.assertRaises(AuthorizationError):
            recovery_director.create_task(
                self.director, self._task("issue_resurrection")
            )

    def test_tenant_offboarding_failure_closes_access_and_resumes(self) -> None:
        self.paperclip.create_task(
            self.director, self._task("issue_offboard_resume")
        )
        self.evidence.put(
            self.strategist,
            self._evidence(
                "evidence_offboard_resume",
                issue_id="issue_offboard_resume",
            ),
        )
        self.artifacts.put(
            self.director, self._learning("learning_offboard_resume")
        )
        self.paperclip.work_queue().cancel_tenant(
            self.director,
            evidence_ref="evidence://offboarding/resume-approved",
        )
        manifest = self.paperclip.prepare_tenant_offboarding(self.director)
        with sqlite3.connect(self.database_path) as connection:
            connection.execute(
                """
                CREATE TRIGGER fail_tenant_offboarding
                BEFORE DELETE ON tenant_evidence
                WHEN OLD.brand_id = 'brand_lantern'
                BEGIN
                    SELECT RAISE(ABORT, 'fictional cleanup failure');
                END
                """
            )

        with self.assertRaises(PlatformAdapterError):
            self.paperclip.offboard_tenant(
                self.director,
                expected_authority_manifest_checksum=manifest[
                    "authority_manifest_checksum"
                ],
                evidence_ref="evidence://offboarding/resume",
            )
        with self.assertRaises(AuthorizationError):
            self.paperclip.get_task(self.director, "issue_offboard_resume")
        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM tenant_evidence
                    WHERE brand_id = ?
                    """,
                    (self.director.brand_id,),
                ).fetchone()[0],
                1,
            )
            connection.execute("DROP TRIGGER fail_tenant_offboarding")

        receipt = self.paperclip.offboard_tenant(
            self.director,
            expected_authority_manifest_checksum=manifest[
                "authority_manifest_checksum"
            ],
            evidence_ref="evidence://offboarding/resume",
        )
        self.assertEqual(
            receipt["authority_manifest_checksum"],
            manifest["authority_manifest_checksum"],
        )
        with sqlite3.connect(self.database_path) as connection:
            self.assertEqual(
                connection.execute(
                    """
                    SELECT COUNT(*) FROM tenant_evidence
                    WHERE brand_id = ?
                    """,
                    (self.director.brand_id,),
                ).fetchone()[0],
                0,
            )
        self.assertEqual(
            self.paperclip.tenant_offboarding_receipt(
                self.director, receipt["tenant_offboarding_receipt_id"]
            ),
            receipt,
        )
        forged = copy.deepcopy(receipt)
        forged.pop("content_checksum")
        forged["evidence_ref"] = "evidence://offboarding/forged"
        forged_seed = {
            key: forged[key]
            for key in (
                "brand_id",
                "authority_manifest_checksum",
                "artifact_deletion_receipt_id",
                "queue_cancellation_receipt_id",
                "manifest_table_row_counts",
                "evidence_ref",
                "requested_by",
                "offboarded_at",
            )
        }
        forged["tenant_offboarding_receipt_id"] = canonical_checksum(
            forged_seed
        )
        forged = finalize_record(forged)
        with sqlite3.connect(self.deletion_ledger_path) as connection:
            connection.execute(
                """
                UPDATE tenant_authority_offboardings
                SET receipt_json = ?
                WHERE authority_id = ? AND brand_id = ?
                """,
                (
                    canonical_bytes(forged).decode(),
                    "fictional_paperclip_approval_authority",
                    self.director.brand_id,
                ),
            )
        with self.assertRaises(PlatformAdapterError):
            self.paperclip.tenant_offboarding_receipt(
                self.director, receipt["tenant_offboarding_receipt_id"]
            )

    def test_queue_is_tenant_role_immutable_and_task_version_bound(self) -> None:
        self.paperclip.create_task(self.director, self._task("issue_queue_bound"))
        draft = self.paperclip.get_task(self.director, "issue_queue_bound")
        ready = self.paperclip.set_status(
            self.director,
            "issue_queue_bound",
            draft["content_checksum"],
            "ready",
        )
        item = self._work_item("work_bound", ready)
        with self.assertRaises(ContractError):
            self._work_item("work_unknown_role", ready, worker_role="invented-role")
        director_queue = self.paperclip.work_queue()
        director_queue.enqueue(self.director, item)
        self.assertEqual(director_queue.enqueue(self.director, item), "work_bound")
        changed = copy.deepcopy(item)
        changed["payload"] = {"fictional_operation": "changed"}
        changed = finalize_record(changed)
        with self.assertRaises(ContractError):
            director_queue.enqueue(self.director, changed)
        with self.assertRaises(KeyError):
            self.foreign_paperclip.work_queue().get(
                self.foreign_director, "work_bound"
            )
        with self.assertRaises(AuthorizationError):
            self.publisher_paperclip.work_queue().get(self.publisher, "work_bound")

        self.paperclip.set_status(
            self.director,
            "issue_queue_bound",
            ready["content_checksum"],
            "in_progress",
        )
        self.assertIsNone(
            self.strategist_paperclip.work_queue().lease_next(self.strategist, 10)
        )
        dead_letter = director_queue.get(self.director, "work_bound")
        self.assertEqual(dead_letter["state"], "DEAD_LETTER")
        self.assertEqual(dead_letter["error_classes"], ["TASK_DRIFT"])

    def test_existing_deletion_ledger_migrates_for_authority_offboarding(
        self,
    ) -> None:
        legacy_ledger_path = self.deletion_ledger_path.with_name(
            "legacy-artifact-deletions.sqlite3"
        )
        with sqlite3.connect(legacy_ledger_path) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE deletion_ledger_metadata (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    authority_id TEXT NOT NULL
                );
                CREATE TABLE tenant_artifact_deletions (
                    authority_id TEXT NOT NULL,
                    brand_id TEXT NOT NULL,
                    receipt_id TEXT NOT NULL UNIQUE,
                    export_checksum TEXT NOT NULL,
                    record_count INTEGER NOT NULL,
                    receipt_json TEXT NOT NULL,
                    deleted_at TEXT NOT NULL,
                    PRIMARY KEY (authority_id, brand_id)
                );
                """
            )
            connection.execute(
                """
                INSERT INTO deletion_ledger_metadata (singleton, authority_id)
                VALUES (1, ?)
                """,
                ("fictional_paperclip_approval_authority",),
            )
        os.chmod(legacy_ledger_path, 0o600)
        migrated_database_path = self.database_path.with_name(
            "legacy-ledger-platform.sqlite3"
        )
        migrated = _provision_platform_authority_host(
            migrated_database_path,
            deletion_ledger_path=legacy_ledger_path,
            authority_id="fictional_paperclip_approval_authority",
            approval_signing_key=self.approval_signing_key,
            initial_time=self.now,
            principals=self.provisioned_principals,
        )
        self.addCleanup(migrated.close)
        director = migrated.client(self.director)
        director.work_queue().cancel_tenant(
            self.director,
            evidence_ref="evidence://offboarding/legacy-ledger",
        )
        manifest = director.prepare_tenant_offboarding(self.director)
        receipt = director.offboard_tenant(
            self.director,
            expected_authority_manifest_checksum=manifest[
                "authority_manifest_checksum"
            ],
            evidence_ref="evidence://offboarding/legacy-ledger",
        )
        self.assertEqual(
            director.tenant_offboarding_receipt(
                self.director, receipt["tenant_offboarding_receipt_id"]
            ),
            receipt,
        )
        with sqlite3.connect(legacy_ledger_path) as connection:
            self.assertEqual(
                {
                    row[1]
                    for row in connection.execute(
                        "PRAGMA table_info(tenant_authority_offboardings)"
                    )
                },
                {
                    "authority_id",
                    "brand_id",
                    "receipt_id",
                    "authority_manifest_checksum",
                    "artifact_deletion_receipt_id",
                    "queue_cancellation_receipt_id",
                    "evidence_ref",
                    "receipt_json",
                    "offboarded_at",
                },
            )

    def test_recovery_host_requires_preprovisioned_deletion_ledger(self) -> None:
        recovery_path = self.database_path.with_name("unprovisioned-recovery.sqlite3")
        missing_ledger = self.database_path.with_name("missing-deletions.sqlite3")
        with self.assertRaises(PlatformAuthorityUnavailable):
            _provision_platform_authority_host(
                recovery_path,
                deletion_ledger_path=missing_ledger,
                authority_id="fictional_paperclip_approval_authority",
                approval_signing_key=self.approval_signing_key,
                initial_time=self.now,
                principals=self.provisioned_principals,
            )
        self.assertFalse(recovery_path.exists())
        self.assertFalse(missing_ledger.exists())

        wrong_authority_path = self.database_path.with_name(
            "wrong-authority-recovery.sqlite3"
        )
        with self.assertRaises(PlatformAuthorityUnavailable):
            _provision_platform_authority_host(
                wrong_authority_path,
                deletion_ledger_path=self.deletion_ledger_path,
                authority_id="another_authority",
                approval_signing_key=self.approval_signing_key,
                initial_time=self.now,
                principals=self.provisioned_principals,
            )
        self.assertFalse(wrong_authority_path.exists())

    def test_replaced_deletion_ledger_identity_is_rejected(self) -> None:
        learning = self._learning("learning_ledger_storage")
        self.artifacts.put(self.director, learning)
        replacement_database = self.database_path.with_name(
            "replacement-ledger-host.sqlite3"
        )
        replacement_ledger = self.database_path.with_name(
            "replacement-deletions.sqlite3"
        )
        replacement_host = _provision_platform_authority_host(
            replacement_database,
            deletion_ledger_path=replacement_ledger,
            initialize_deletion_ledger=True,
            authority_id="fictional_paperclip_approval_authority",
            approval_signing_key=self.approval_signing_key,
            initial_time=self.now,
            principals=self.provisioned_principals,
        )
        replacement_host.close()
        replacement_ledger.replace(self.deletion_ledger_path)

        with self.assertRaises(ArtifactStoreError):
            self.artifacts.get(self.director, "learning_ledger_storage")

    def test_replaced_storage_identity_is_rejected_by_all_authorities(self) -> None:
        self.paperclip.create_task(self.director, self._task("issue_asset"))
        task = self._advance_to_in_progress("issue_asset")
        self.paperclip.work_queue().enqueue(
            self.director, self._work_item("work_storage", task)
        )
        self.artifacts.put(self.director, self._learning("learning_storage"))
        replacement_path = self.database_path.with_name("replacement.sqlite3")
        replacement_host = _provision_platform_authority_host(
            replacement_path,
            deletion_ledger_path=self.deletion_ledger_path,
            authority_id="fictional_paperclip_approval_authority",
            approval_signing_key=os.urandom(32),
            initial_time=self.now,
            principals=self.provisioned_principals,
        )
        replacement_host.close()
        replacement_path.replace(self.database_path)

        with self.assertRaises(PlatformAdapterError):
            self.paperclip.get_task(self.director, "issue_asset")
        with self.assertRaises(EvidenceStoreError):
            self.evidence.get(self.strategist, "evidence_primary")
        with self.assertRaises(ArtifactStoreError):
            self.artifacts.get(self.director, "learning_storage")
        with self.assertRaises(WorkQueueError):
            self.paperclip.work_queue().get(self.director, "work_storage")

    def test_audit_is_persistent_and_tenant_scoped(self) -> None:
        self.paperclip.create_task(self.director, self._task("issue_asset"))
        self.evidence.put(self.strategist, self._evidence())
        restarted = self.platform_host.client(self.director)
        events = restarted.audit_events(self.director)
        self.assertEqual(
            [event["event_type"] for event in events],
            ["paperclip.task.created", "evidence.recorded"],
        )
        self.assertEqual(
            self.foreign_paperclip.audit_events(self.foreign_director),
            [],
        )


if __name__ == "__main__":
    unittest.main()
