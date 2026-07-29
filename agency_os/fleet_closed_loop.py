"""Fleet finding-to-content controlled proof using the existing Core workflow."""

from __future__ import annotations

from typing import Any, Callable, Mapping

from .brand_intelligence import ApprovalBinding, BrandIntelligenceAuthority, make_generation2_record
from .contracts import ContractError, canonical_checksum
from .core_workflow import CoreWorkflowResult, run_core_workflow
from .gateway import MockPublisher
from .store import Principal


def build_remediation_proposal(
    authority: BrandIntelligenceAuthority,
    *,
    director: Principal,
    finding_id: str,
    paperclip_issue_id: str,
) -> dict[str, Any]:
    finding = authority.get(director, "market_finding", finding_id)
    return make_generation2_record(
        "remediation_proposal", brand_id=director.brand_id, created_by=director.actor_id,
        provenance=finding["provenance"], created_at="2026-07-29T12:04:18Z",
        effective_at="2026-07-29T12:04:18Z",
        proposal_id="proposal_fleet_ai_brand_explainer_v1", finding_id=finding_id,
        proposed_change=(
            "Create a plain-English Fleet explainer, grounded in approved Brand Twin claims, "
            "and deploy it only to a controlled preview."
        ),
        expected_effect=(
            "The preview should answer the selected customer mission accurately. Public-search "
            "visibility is not expected until a later public destination is separately approved."
        ),
        risk={
            "unsupported_claims": "blocked by Brand Twin grounding and QA",
            "external_write": "controlled mock preview only",
            "causal_overclaim": "must remain unknown without stronger evidence",
        },
        approval_state="approved", paperclip_issue_id=paperclip_issue_id, status="active",
    )


def remediation_approval_package(
    proposal: Mapping[str, Any], finding: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "artifact_type": "remediation_approval_package",
        "brand_id": proposal["brand_id"],
        "proposal_id": proposal["proposal_id"],
        "proposal_checksum": proposal["content_checksum"],
        "finding_id": finding["finding_id"],
        "finding_checksum": finding["content_checksum"],
        "destination_class": "controlled_mock_preview",
    }


def remediation_binding_from_paperclip(
    approval: Mapping[str, Any], expected_package: Mapping[str, Any], *, approved_by: str,
) -> ApprovalBinding:
    if approval.get("status") != "approved" or approval.get("payload") != dict(expected_package):
        raise ContractError("Paperclip did not approve the exact Fleet remediation package")
    approval_id = str(approval.get("id", ""))
    approved_at = str(approval.get("updatedAt") or approval.get("decidedAt") or "")
    if not approval_id or not approved_at:
        raise ContractError("Paperclip remediation approval identity is incomplete")
    return ApprovalBinding(
        paperclip_approval_id=approval_id,
        approval_checksum=canonical_checksum(approval),
        approved_by=approved_by,
        approved_at=approved_at,
    )


def run_fleet_closed_loop(
    authority: BrandIntelligenceAuthority,
    *,
    director: Principal,
    analyst: Principal,
    reader: Principal,
    paperclip: Any,
    buzz: Any,
    publication_approval_authority: Callable[[dict[str, Any], dict[str, Any]], Any],
    remediation_approval: Mapping[str, Any],
    approved_by: str,
    paperclip_issue_id: str,
    publisher: MockPublisher | None = None,
    cost_agent_id: str | None = None,
) -> dict[str, Any]:
    finding_id = "finding_fleet_public_explainer_gap_v1"
    finding = authority.get(reader, "market_finding", finding_id)
    proposal = build_remediation_proposal(
        authority, director=director, finding_id=finding_id,
        paperclip_issue_id=paperclip_issue_id,
    )
    package = remediation_approval_package(proposal, finding)
    binding = remediation_binding_from_paperclip(
        remediation_approval, package, approved_by=approved_by,
    )
    authority.put(director, proposal, approval=binding)
    grounding = authority.content_grounding(
        reader,
        required_claim_ids=[
            "claim_fleet_business_name", "claim_fleet_unified_product",
            "claim_content_engine_first_class", "claim_paperclip_authority",
            "claim_real_providers_unconnected",
        ],
        at="2026-07-29T12:04:18Z",
    )
    publisher = publisher or MockPublisher(
        destination_ref="mock_preview:fleet",
        endpoint="mock://preview/fleet",
        expected_credential="fictional-credential-lantern",
    )
    workflow: CoreWorkflowResult = run_core_workflow(
        paperclip=paperclip, buzz=buzz,
        approval_authority=publication_approval_authority,
        publisher=publisher,
        campaign_id="campaign_fleet_explainer_v1",
        asset_id="asset_fleet_ai_brand_explainer_v1", brand_name="Fleet",
        brand_grounding=grounding, cost_agent_id=cost_agent_id,
        content_scenario={
            "topic": "AI-ready brand operations",
            "audience": "brand leaders",
            "objective": "Explain how Fleet combines governed content production with AI brand readiness.",
            "information_gain": "A plain-English view of Fleet's approved operating model and its current limits.",
            "cta": "Ask Fleet for an AI brand readiness review.",
            "supported_claim_text": (
                "Fleet is building one governed platform that combines automated content production "
                "with evidence-backed AI brand operations."
            ),
            "content_description": "A controlled Fleet explainer grounded in approved Brand Twin claims.",
            "destination_ref": "mock_preview:fleet",
            "credential_endpoint": "mock://preview/fleet",
            "internal_note": "Controlled preview only; no public website or provider write.",
            "public_fields": {
                "title": "How Fleet helps brands become ready for the AI economy",
                "body": [
                    "Fleet combines governed automated content production with an evidence-backed view of brand truth.",
                    "Paperclip keeps work and approvals authoritative, while the Brand Twin supplies approved claims and policies.",
                    "The current proof uses a controlled preview. It does not claim public-search or external-AI improvement.",
                ],
                "cta": "Ask Fleet for an AI brand readiness review.",
            },
        },
    )
    manifest = workflow.records["published_manifest"]
    receipt = workflow.records["published_receipt"]
    experiment = make_generation2_record(
        "experiment", brand_id=analyst.brand_id, created_by=analyst.actor_id,
        provenance=finding["provenance"], created_at="2026-07-29T12:04:18Z",
        effective_at="2026-07-29T12:04:18Z",
        experiment_id="experiment_fleet_controlled_explainer_v1",
        proposal_id=proposal["proposal_id"],
        hypothesis="An approved Brand Twin can ground a complete content workflow without unsupported claims or a public provider write.",
        baseline_refs=finding["observation_refs"],
        treatment={
            "manifest_id": manifest["manifest_id"],
            "manifest_checksum": manifest["content_checksum"],
            "destination_ref": manifest["destination_ref"],
        },
        evaluation_plan={
            "controlled_preview": "must publish exactly once after exact approval",
            "public_search_retest": "repeat the same launch missions and report observed, inferred and unknown separately",
        },
        status="complete",
    )
    authority.put(analyst, experiment)
    outcome = make_generation2_record(
        "outcome_event", brand_id=analyst.brand_id, created_by=analyst.actor_id,
        provenance=finding["provenance"], created_at="2026-07-29T12:04:18Z",
        effective_at="2026-07-29T12:04:18Z",
        outcome_id="outcome_fleet_controlled_preview_v1",
        experiment_id=experiment["experiment_id"], metric="controlled_preview_publications",
        value=1, unit="exact_approved_mock_write", source_ref=finding["provenance"][0],
        observed_at="2026-07-29T12:04:18Z", causal_status="observed", status="active",
    )
    authority.put(analyst, outcome)
    if workflow.external_writes or publisher.calls != 1 or receipt["state"] != "PUBLISHED":
        raise ContractError("Fleet controlled content proof did not satisfy its safe-write boundary")
    return {
        "status": "pass", "brand_id": "brand_fleet",
        "finding_id": finding_id, "proposal_id": proposal["proposal_id"],
        "remediation_approval_id": binding.paperclip_approval_id,
        "publication_approval_id": workflow.approval["id"],
        "manifest_checksum": manifest["content_checksum"],
        "receipt_checksum": receipt["content_checksum"],
        "paperclip_task_count": len(workflow.tasks_by_role),
        "paperclip_tasks_done": all(item["status"] == "done" for item in workflow.tasks_by_role.values()),
        "publisher_calls": publisher.calls, "external_writes": workflow.external_writes,
        "known": "The approved content completed the full Core path and one controlled mock preview write.",
        "inference": "The same governed pattern is suitable for a later separately approved public explainer.",
        "unknown": "No public-search or external-AI improvement is established by the controlled preview.",
        "workflow": workflow,
    }
