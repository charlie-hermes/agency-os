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
    _provision_platform_authority_host,
)
from agency_os.platform_adapters import (
    _AuthorityPaperclipAdapter,
    _SQLiteArtifactDeletionLedger,
    ArtifactStoreError,
    EvidenceStoreError,
    FictionalBuzzAdapter,
    PlatformAdapterError,
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
        self.provisioned_principals = (
            self.director,
            self.foreign_director,
            self.approver,
            self.unlisted_approver,
            self.strategist,
            self.publisher,
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

        receipt = self.artifacts.delete_tenant(
            self.director, tenant_export["export_checksum"]
        )
        receipt_id = receipt["deletion_receipt_id"]
        self.assertEqual(receipt["record_count"], 1)
        self.assertNotIn("validated_correction", canonical_bytes(receipt).decode())
        artifact_audit = self.paperclip.audit_events(self.director)
        self.assertEqual(
            [event["event_type"] for event in artifact_audit],
            ["artifact.tenant_deleted"],
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
        for forbidden_export in (
            "FictionalApprovalAuthority",
            "FictionalRecoveryAuthority",
            "FictionalPaperclipAdapter",
            "SQLiteTenantEvidenceStore",
            "SQLiteTenantArtifactStore",
            "SQLiteArtifactDeletionLedger",
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
