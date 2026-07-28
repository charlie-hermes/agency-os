"""Complete fictional Search Authority Core proof on installed API bindings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping

from .contracts import finalize_record, make_approval_record
from .gateway import MockPublisher
from .integrations import PaperclipLifecycleAdapter, TypedBuzzAdapter
from .operator_view import build_campaign_projection
from .workflow import VerticalSliceResult, dispatch_prepared_article, prepare_fictional_article


CORE_RUNTIME_ROLES = (
    "agency-director",
    "brand-brief-steward",
    "search-content-strategist",
    "content-producer",
    "search-answer-optimiser",
    "editorial-integrity-qa",
    "publishing-operator",
    "growth-intelligence-analyst",
)


class CoreApprovalDenied(RuntimeError):
    """The Paperclip board did not approve the exact publication manifest."""


ApprovalAuthority = Callable[
    [dict[str, Any], Mapping[str, Any]], dict[str, Any]
]


@dataclass
class CoreWorkflowResult:
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
            "brand_id": "brand_lantern",
            "campaign_id": "camp_summer",
            "asset_id": "asset_guide",
            "paperclip_issue_id": issue_id,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": {"actor_type": "agent", "actor_id": role_id},
            "source_artifact_ids": list(source_artifact_ids or []),
            "status": status,
            "payload": payload,
        }
    )


def run_core_workflow(
    *,
    paperclip: PaperclipLifecycleAdapter,
    buzz: TypedBuzzAdapter,
    approval_authority: ApprovalAuthority,
    publisher: MockPublisher,
) -> CoreWorkflowResult:
    """Run onboarding through learning with a real reject/revise branch."""

    definitions = (
        ("agency-director", "Direct Lantern Core campaign", "campaign_direction", ["campaign graph accepted"], ["campaign_brief_v1"]),
        ("brand-brief-steward", "Validate Lantern brand and brief", "onboarding", ["brand truth and constraints approved"], ["brand_profile_v1", "campaign_brief_v1"]),
        ("search-content-strategist", "Research and plan balcony guide", "research_strategy", ["sources and opportunity plan retained"], ["source_observation_v1", "research_pack_v1", "content_plan_v1", "content_brief_v1"]),
        ("content-producer", "Draft and revise balcony guide", "drafting", ["rejected checksum revised without unsupported claim"], ["draft_rejected_v0", "draft_guide_v1"]),
        ("search-answer-optimiser", "Optimise revised guide", "search_optimisation", ["complete package is checksum-bound"], ["complete_guide_v1"]),
        ("editorial-integrity-qa", "Independently QA guide", "quality_assurance", ["one REVISE then PASS is recorded"], ["qa_revise_v0", "qa_guide_v1"]),
        ("publishing-operator", "Publish approved guide to sandbox", "publication", ["exact approved manifest reaches mock gateway"], ["manifest_guide_v1", "receipt_idem_guide_v1"]),
        ("growth-intelligence-analyst", "Validate and measure publication", "measurement_learning", ["validation, proposal and learning disposition retained"], ["performance_guide_v1", "optimisation_proposal_v1", "learning_guide_v1"]),
    )
    tasks_by_role: dict[str, dict[str, Any]] = {}
    previous_child_id: str | None = None
    root_id: str | None = None
    for role_id, title, stage, criteria, artifact_refs in definitions:
        is_director = role_id == "agency-director"
        task = paperclip.create_task(
            title=title,
            campaign_id="camp_summer",
            stage=stage,
            acceptance_criteria=criteria,
            parent_id=None if is_director else root_id,
            blocked_by_issue_ids=(
                [previous_child_id] if previous_child_id is not None else []
            ),
            status="todo",
            idempotency_key=f"lantern-core-{role_id}",
            artifact_refs=artifact_refs,
        )
        tasks_by_role[role_id] = task
        if is_director:
            root_id = task["id"]
        else:
            previous_child_id = task["id"]

    records: dict[str, dict[str, Any]] = {}
    records["brand_profile"] = _artifact(
        "brand_profile", "brand_profile_v1",
        issue_id=tasks_by_role["brand-brief-steward"]["id"],
        role_id="brand-brief-steward",
        payload={"brand_name": "Lantern Garden Co.", "prohibited_claims": ["guaranteed results"], "approval_owner": "human_owner"},
    )
    records["campaign_brief"] = _artifact(
        "campaign_brief", "campaign_brief_v1",
        issue_id=tasks_by_role["agency-director"]["id"], role_id="agency-director",
        payload={"objective": "Help apartment gardeners choose a safe first project.", "product": "search_authority_core", "budget_mode": "fictional"},
        source_artifact_ids=["brand_profile_v1"],
    )
    records["source_observation"] = _artifact(
        "source_observation", "source_observation_v1",
        issue_id=tasks_by_role["search-content-strategist"]["id"], role_id="search-content-strategist",
        payload={"source_class": "approved_brand_fact", "supported_proposition": "The guide contains five fictional checklist steps.", "retrieval_mode": "fixture"},
        source_artifact_ids=["brand_profile_v1"],
    )
    records["research_pack"] = _artifact(
        "research_pack", "research_pack_v1",
        issue_id=tasks_by_role["search-content-strategist"]["id"], role_id="search-content-strategist",
        payload={"observations": ["source_observation_v1"], "evidence_gaps": [], "audience_need": "a concrete pre-start checklist"},
        source_artifact_ids=["source_observation_v1"],
    )
    records["content_plan"] = _artifact(
        "content_plan", "content_plan_v1",
        issue_id=tasks_by_role["search-content-strategist"]["id"], role_id="search-content-strategist",
        payload={"asset_type": "article", "angle": "five checks", "success_signal": "sandbox publication integrity"},
        source_artifact_ids=["research_pack_v1"],
    )
    records["content_brief"] = _artifact(
        "content_brief", "content_brief_v1",
        issue_id=tasks_by_role["search-content-strategist"]["id"], role_id="search-content-strategist",
        payload={"audience": "Apartment gardeners", "cta": "Download the fictional worksheet.", "required_claim_ids": ["claim_1"]},
        source_artifact_ids=["content_plan_v1", "research_pack_v1"],
    )
    records["rejected_draft"] = _artifact(
        "draft_asset_package", "draft_rejected_v0",
        issue_id=tasks_by_role["content-producer"]["id"], role_id="content-producer",
        payload={"public_fields": {"title": "Guaranteed balcony-garden success", "body": ["Follow these steps for guaranteed results."]}, "internal_notes": ["unsupported guarantee deliberately injected for QA proof"], "claim_register": [{"claim_id": "claim_bad", "status": "pending_authority"}], "source_register": []},
        source_artifact_ids=["content_brief_v1"], status="rejected",
    )
    records["qa_revise"] = _artifact(
        "qa_verdict", "qa_revise_v0",
        issue_id=tasks_by_role["editorial-integrity-qa"]["id"], role_id="editorial-integrity-qa",
        payload={"verdict": "REVISE", "reviewed_checksum": records["rejected_draft"]["content_checksum"], "findings": [{"code": "UNSUPPORTED_CLAIM", "owning_stage": "drafting", "claim_id": "claim_bad"}]},
        source_artifact_ids=["draft_rejected_v0"], status="rejected",
    )

    producer_task = tasks_by_role["content-producer"]
    paperclip.update_task(producer_task["id"], status="in_progress", comment=f"QA REVISE binds {records['rejected_draft']['content_checksum']}; remove unsupported guarantee.")
    channel = buzz.create_context_channel(campaign_id="camp_summer", purpose="Resolve QA finding UNSUPPORTED_CLAIM", ttl_seconds=900)
    buzz.post_context(channel["id"], {"brand_id": "brand_lantern", "campaign_id": "camp_summer", "paperclip_issue_id": producer_task["id"], "decision_needed": "revision disposition", "exit_condition": "evidence-safe wording agreed"})
    decision = buzz.post_decision(channel["id"], paperclip_issue_id=producer_task["id"], decision="Remove the guarantee; retain only the supported five-check decision aid.", evidence_refs=["qa_revise_v0", "source_observation_v1"])
    writeback = paperclip.comment(producer_task["id"], f"Buzz decision write-back {decision['id']}: remove guarantee; evidence qa_revise_v0 and source_observation_v1.")
    records["buzz_decision_writeback"] = _artifact(
        "buzz_decision_writeback",
        "buzz_decision_writeback_v1",
        issue_id=producer_task["id"],
        role_id="content-producer",
        payload={
            "buzz_message_id": decision["id"],
            "buzz_channel_id": channel["id"],
            "paperclip_comment_id": writeback["id"],
            "decision_authority": "paperclip_writeback",
        },
        source_artifact_ids=["qa_revise_v0", "source_observation_v1"],
    )

    prepared = prepare_fictional_article(
        issue_id=tasks_by_role["publishing-operator"]["id"],
        publisher=publisher,
    )
    manifest = prepared.records["manifest"]
    requested_approval = paperclip.request_approval(
        issue_ids=[tasks_by_role["publishing-operator"]["id"]],
        manifest=manifest,
    )
    approval_authority(requested_approval, manifest)
    approval = paperclip.get_approval(requested_approval["id"])
    if (
        approval.get("id") != requested_approval["id"]
        or approval.get("status") != "approved"
        or approval.get("payload") != manifest
    ):
        raise CoreApprovalDenied("Paperclip did not approve the exact manifest")
    approval_issues = paperclip.get_approval_issues(approval["id"])
    if [item["id"] for item in approval_issues] != requested_approval["issueIds"]:
        raise CoreApprovalDenied("Paperclip approval issue binding changed")
    decided_at = datetime.now(timezone.utc)
    approval_evidence = finalize_record(
        {
            "schema_version": "1.0",
            "artifact_type": "paperclip_approval_evidence",
            "brand_id": paperclip.brand_id,
            "company_id": paperclip.company_id,
            "paperclip_approval_id": approval["id"],
            "status": approval["status"],
            "issue_ids": [item["id"] for item in approval_issues],
            "manifest_checksum": manifest["content_checksum"],
            "decision_note": approval.get("decisionNote"),
            "observed_at": decided_at.isoformat(),
        }
    )
    records["paperclip_approval_evidence"] = approval_evidence
    gateway_approval = make_approval_record(
        approval_id="approval_guide_v1",
        manifest=manifest,
        approver_id="human_owner",
        authority_role="brand_owner",
        decided_at=decided_at.isoformat(),
        expires_at=(decided_at + timedelta(minutes=30)).isoformat(),
        paperclip_approval_id=approval["id"],
        paperclip_approval_evidence_checksum=approval_evidence["content_checksum"],
    )
    vertical = dispatch_prepared_article(prepared, approval=gateway_approval)
    records.update({f"published_{key}": value for key, value in vertical.records.items()})
    records["optimisation_proposal"] = _artifact(
        "optimisation_proposal", "optimisation_proposal_v1",
        issue_id=tasks_by_role["growth-intelligence-analyst"]["id"], role_id="growth-intelligence-analyst",
        payload={"proposal": "Collect a later fictional outcome snapshot before changing content.", "evidence_refs": ["performance_guide_v1"], "authority": "proposal_only"},
        source_artifact_ids=["performance_guide_v1"], status="draft",
    )
    paperclip.record_cost(
        {
            "agentId": "00000000-0000-4000-8000-000000000008",
            "provider": "fictional_fixture", "model": "no-model",
            "costCents": 0,
            "occurredAt": datetime.now(timezone.utc).isoformat(),
        }
    )

    closure_order = (*CORE_RUNTIME_ROLES[1:], CORE_RUNTIME_ROLES[0])
    for role_id in closure_order:
        task = paperclip.get_task(tasks_by_role[role_id]["id"])
        if task["status"] != "done":
            paperclip.update_task(
                task["id"], status="done",
                comment=f"{role_id} acceptance evidence verified; authoritative closure recorded.",
            )
        tasks_by_role[role_id] = paperclip.get_task(task["id"])

    projection = build_campaign_projection(
        paperclip,
        campaign_id="camp_summer",
        task_ids=[item["id"] for item in tasks_by_role.values()],
        approval_ids=[approval["id"]],
    )
    return CoreWorkflowResult(
        vertical_slice=vertical,
        tasks_by_role=tasks_by_role,
        records=records,
        approval=approval,
        operator_projection=projection,
    )
