"""Optional Social Amplifier branch from an approved canonical Core asset."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from .contracts import finalize_record, make_approval_record
from .core_workflow import CoreWorkflowResult
from .gateway import MockPublisher
from .integrations import PaperclipLifecycleAdapter, TypedBuzzAdapter
from .operator_view import build_campaign_projection
from .workflow import VerticalSliceResult, dispatch_prepared_article, prepare_fictional_article


SOCIAL_BRANCH_ROLES = (
    "visual-creative-specialist",
    "social-amplifier",
    "editorial-integrity-qa",
    "publishing-operator",
    "growth-intelligence-analyst",
)


class SocialAmplifierDenied(RuntimeError):
    """The optional branch is disabled or its exact approval is missing."""


ApprovalAuthority = Callable[[dict[str, Any], Mapping[str, Any]], dict[str, Any]]


@dataclass
class SocialWorkflowResult:
    vertical_slice: VerticalSliceResult
    tasks_by_role: dict[str, dict[str, Any]]
    records: dict[str, dict[str, Any]]
    approval: dict[str, Any]
    operator_projection: dict[str, Any]
    external_writes: bool = False


def _artifact(
    artifact_type: str,
    artifact_id: str,
    *,
    brand_id: str,
    campaign_id: str,
    asset_id: str,
    issue_id: str,
    role_id: str,
    payload: dict[str, Any],
    source_artifact_ids: list[str] | None = None,
    status: str = "approved",
) -> dict[str, Any]:
    return finalize_record(
        {
            "schema_version": "1.0",
            "artifact_type": artifact_type,
            "artifact_id": artifact_id,
            "brand_id": brand_id,
            "campaign_id": campaign_id,
            "asset_id": asset_id,
            "paperclip_issue_id": issue_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": {"actor_type": "agent", "actor_id": role_id},
            "source_artifact_ids": list(source_artifact_ids or []),
            "status": status,
            "payload": payload,
        }
    )


def run_social_workflow(
    *,
    core: CoreWorkflowResult,
    paperclip: PaperclipLifecycleAdapter,
    buzz: TypedBuzzAdapter,
    approval_authority: ApprovalAuthority,
    publisher: MockPublisher,
    campaign_id: str = "camp_summer_social",
    asset_id: str = "asset_guide_social",
) -> SocialWorkflowResult:
    """Run the optional branch only after exact Core approval and publication."""

    product = core.records.get("campaign_brief", {}).get("payload", {}).get("product")
    if product != "search_authority_social":
        raise SocialAmplifierDenied("Social Amplifier is not enabled for this campaign")
    core_manifest = core.records.get("published_manifest", {})
    core_receipt = core.records.get("published_receipt", {})
    core_qa = core.records.get("published_qa_verdict", {})
    brand_id = paperclip.brand_id
    if buzz.brand_id != brand_id or core_manifest.get("brand_id") != brand_id:
        raise SocialAmplifierDenied("Social Amplifier brand binding changed")
    if core.approval.get("status") != "approved":
        raise SocialAmplifierDenied("Canonical Core approval is not approved")
    if core_receipt.get("state") != "PUBLISHED":
        raise SocialAmplifierDenied("Canonical Core publication is not complete")
    if core_qa.get("payload", {}).get("verdict") != "PASS":
        raise SocialAmplifierDenied("Canonical Core QA did not pass")

    root_id = core.tasks_by_role["agency-director"]["id"]
    definitions = (
        ("visual-creative-specialist", "Create approved social visual", "social_visual", ["visual provenance and checksum retained"]),
        ("social-amplifier", "Build channel-native social package", "social_adaptation", ["copy derives only from the canonical asset"]),
        ("editorial-integrity-qa", "Independently QA social package", "social_quality_assurance", ["exact social checksum receives PASS"]),
        ("publishing-operator", "Publish approved social package to sandbox", "social_publication", ["exact approved manifest reaches the gateway"]),
        ("growth-intelligence-analyst", "Validate social publication", "social_measurement", ["receipt and evidence-limited snapshot retained"]),
    )
    tasks_by_role: dict[str, dict[str, Any]] = {}
    previous: str | None = None
    for role_id, title, stage, criteria in definitions:
        task = paperclip.create_task(
            title=title,
            campaign_id=campaign_id,
            stage=stage,
            acceptance_criteria=criteria,
            parent_id=root_id,
            blocked_by_issue_ids=[previous] if previous else [],
            status="todo",
            idempotency_key=f"{brand_id}-{campaign_id}-{role_id}",
            artifact_refs=[f"{stage}_v1"],
        )
        tasks_by_role[role_id] = task
        previous = task["id"]

    def artifact(
        artifact_type: str,
        artifact_id: str,
        *,
        role_id: str,
        payload: dict[str, Any],
        source_artifact_ids: list[str] | None = None,
        status: str = "approved",
    ) -> dict[str, Any]:
        return _artifact(
            artifact_type,
            artifact_id,
            brand_id=brand_id,
            campaign_id=campaign_id,
            asset_id=asset_id,
            issue_id=tasks_by_role[role_id]["id"],
            role_id=role_id,
            payload=payload,
            source_artifact_ids=source_artifact_ids,
            status=status,
        )

    records: dict[str, dict[str, Any]] = {}
    records["visual_manifest"] = artifact(
        "visual_asset_manifest",
        "social_visual_v1",
        role_id="visual-creative-specialist",
        payload={
            "creator_class": "human_created_fixture",
            "canonical_asset_checksum": core_manifest["content_checksum"],
            "asset_ref": "mock://creative/social-card-v1",
            "alt_text": "Five checks before starting a balcony garden.",
            "provider_mode": "manual_handoff",
        },
        source_artifact_ids=[core_manifest["manifest_id"]],
    )
    records["social_plan"] = artifact(
        "social_distribution_plan",
        "social_plan_v1",
        role_id="social-amplifier",
        payload={
            "channels": ["fictional_professional_network"],
            "sequence": ["single_organic_post"],
            "canonical_asset_checksum": core_manifest["content_checksum"],
            "paid_media": False,
            "engagement_automation": False,
        },
        source_artifact_ids=["social_visual_v1", core_manifest["manifest_id"]],
    )
    public_fields = {
        "title": "Five checks before you start a balcony garden",
        "body": [
            "Check light, wind, space, water access, and building rules before choosing your first project.",
            "The linked fictional worksheet helps you record each answer.",
        ],
        "cta": "Read the approved fictional guide.",
        "visual_ref": records["visual_manifest"]["payload"]["asset_ref"],
    }
    records["social_package"] = artifact(
        "social_asset_package",
        "social_package_v1",
        role_id="social-amplifier",
        payload={
            "public_fields": public_fields,
            "canonical_asset_checksum": core_manifest["content_checksum"],
            "prohibited_claims": ["guaranteed results"],
            "channel_policy": "organic_fixture_only",
        },
        source_artifact_ids=["social_plan_v1", "social_visual_v1"],
    )
    records["social_qa"] = artifact(
        "qa_verdict",
        "social_qa_v1",
        role_id="editorial-integrity-qa",
        payload={
            "verdict": "PASS",
            "reviewed_checksum": records["social_package"]["content_checksum"],
            "findings": [],
        },
        source_artifact_ids=["social_package_v1"],
    )

    channel = buzz.create_context_channel(
        campaign_id=campaign_id,
        purpose="Confirm social package derives from the approved canonical asset",
        ttl_seconds=900,
    )
    buzz.post_context(
        channel["id"],
        {
            "brand_id": brand_id,
            "campaign_id": campaign_id,
            "paperclip_issue_id": tasks_by_role["social-amplifier"]["id"],
            "decision_needed": "social lineage disposition",
            "exit_condition": "canonical checksum and QA PASS confirmed",
        },
    )
    decision = buzz.post_decision(
        channel["id"],
        paperclip_issue_id=tasks_by_role["social-amplifier"]["id"],
        decision="Use only the approved social package and visual checksum.",
        evidence_refs=["social_package_v1", "social_qa_v1"],
    )
    writeback = paperclip.comment(
        tasks_by_role["social-amplifier"]["id"],
        f"Buzz decision {decision['id']} written back; exact social package and QA evidence bound.",
    )
    records["buzz_decision_writeback"] = artifact(
        "buzz_decision_writeback",
        "social_buzz_writeback_v1",
        role_id="social-amplifier",
        payload={
            "buzz_message_id": decision["id"],
            "buzz_channel_id": channel["id"],
            "paperclip_comment_id": writeback["id"],
            "decision_authority": "paperclip_writeback",
        },
        source_artifact_ids=["social_package_v1", "social_qa_v1"],
    )

    brand_name = core.records["brand_profile"]["payload"]["brand_name"]
    prepared = prepare_fictional_article(
        issue_id=tasks_by_role["publishing-operator"]["id"],
        publisher=publisher,
        brand_id=brand_id,
        campaign_id=campaign_id,
        asset_id=asset_id,
        brand_name=brand_name,
        public_fields=public_fields,
        content_description="An approved fictional social adaptation of the canonical guide.",
    )
    manifest = prepared.records["manifest"]
    requested = paperclip.request_approval(
        issue_ids=[tasks_by_role["publishing-operator"]["id"]],
        manifest=manifest,
    )
    approval_authority(requested, manifest)
    approval = paperclip.get_approval(requested["id"])
    if approval.get("status") != "approved" or approval.get("payload") != manifest:
        raise SocialAmplifierDenied("Paperclip did not approve the exact social manifest")
    approval_issues = paperclip.get_approval_issues(approval["id"])
    if [item["id"] for item in approval_issues] != requested["issueIds"]:
        raise SocialAmplifierDenied("Social approval issue binding changed")

    decided_at = datetime.now(timezone.utc)
    evidence = finalize_record(
        {
            "schema_version": "1.0",
            "artifact_type": "paperclip_approval_evidence",
            "brand_id": brand_id,
            "company_id": paperclip.company_id,
            "paperclip_approval_id": approval["id"],
            "status": approval["status"],
            "issue_ids": [item["id"] for item in approval_issues],
            "manifest_checksum": manifest["content_checksum"],
            "decision_note": approval.get("decisionNote"),
            "observed_by": prepared.principals["paperclip"].actor_id,
            "observed_at": decided_at.isoformat(),
        }
    )
    prepared.store.put(prepared.principals["paperclip"], evidence)
    records["paperclip_approval_evidence"] = evidence
    gateway_approval = make_approval_record(
        approval_id="approval_social_v1",
        manifest=manifest,
        approver_id="human_owner",
        authority_role="brand_owner",
        decided_at=decided_at.isoformat(),
        expires_at=(decided_at + timedelta(minutes=30)).isoformat(),
        paperclip_approval_id=approval["id"],
        paperclip_approval_evidence_checksum=evidence["content_checksum"],
    )
    vertical = dispatch_prepared_article(
        prepared,
        approval=gateway_approval,
        idempotency_key=f"{brand_id}-{campaign_id}-social-v1",
    )
    records.update({f"published_{key}": value for key, value in vertical.records.items()})
    records["social_performance"] = artifact(
        "social_performance_snapshot",
        "social_performance_v1",
        role_id="growth-intelligence-analyst",
        payload={
            "publication_receipt_id": vertical.records["receipt"]["receipt_id"],
            "metrics": {},
            "conclusion_class": "insufficient_evidence",
            "provider_mode": "manual_handoff",
        },
        source_artifact_ids=[vertical.records["receipt"]["receipt_id"]],
    )

    for role_id in SOCIAL_BRANCH_ROLES:
        task = tasks_by_role[role_id]
        paperclip.update_task(
            task["id"],
            status="done",
            comment=f"{role_id} social acceptance evidence verified.",
        )
        tasks_by_role[role_id] = paperclip.get_task(task["id"])
    projection = build_campaign_projection(
        paperclip,
        campaign_id=campaign_id,
        task_ids=[item["id"] for item in tasks_by_role.values()],
        approval_ids=[approval["id"]],
    )
    return SocialWorkflowResult(
        vertical_slice=vertical,
        tasks_by_role=tasks_by_role,
        records=records,
        approval=approval,
        operator_projection=projection,
    )
