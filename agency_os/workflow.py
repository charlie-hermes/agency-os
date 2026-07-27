"""One fictional article flow through the Phase 0/1 controls."""

from __future__ import annotations

import multiprocessing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .capabilities import CapabilityRegistry
from .contracts import (
    finalize_record,
    make_approval_record,
    make_capability_record,
    make_envelope,
    make_publication_manifest,
)
from .gateway import GatewayDenied, MockPublisher
from .gateway_host import (
    _authority_enrollment_for_process,
    _provision_authority_gateway_host,
    fictional_runtime,
)
from .ledger import InMemoryActionLedger
from .runtime_security import fictional_credential_broker
from .store import Principal, TenantStore


@dataclass
class VerticalSliceResult:
    store: TenantStore
    publisher: MockPublisher
    records: dict[str, dict[str, Any]]


def _fictional_publish_worker(control: Any) -> None:
    """Worker entrypoint: receive only a socket path and publication request."""

    try:
        request = control.recv()
        client = fictional_runtime(request["socket_path"])
        receipt = client.publish(
            manifest=request["manifest"],
            approval_id=request["approval_id"],
            idempotency_key=request["idempotency_key"],
        )
        control.send({"outcome": "ALLOW", "receipt": receipt})
    except (EOFError, KeyError, GatewayDenied) as exc:
        control.send({"outcome": "DENY", "code": str(exc)})
    finally:
        control.close()


def run_fictional_article() -> VerticalSliceResult:
    brand_id = "brand_lantern"
    campaign_id = "camp_summer"
    asset_id = "asset_guide"
    issue_id = "pc_100"
    store = TenantStore()
    records: dict[str, dict[str, Any]] = {}

    principals = {
        "steward": Principal("agent_steward", "brand-brief-steward", brand_id),
        "strategist": Principal(
            "agent_strategist", "search-content-strategist", brand_id
        ),
        "producer": Principal("agent_producer", "content-producer", brand_id),
        "optimiser": Principal(
            "agent_optimiser", "search-answer-optimiser", brand_id
        ),
        "qa": Principal("agent_qa", "editorial-integrity-qa", brand_id),
        "director": Principal("agent_director", "agency-director", brand_id),
        "approver": Principal("human_owner", "human-approver", brand_id),
        "publisher": Principal(
            "agent_publisher", "publishing-operator", brand_id
        ),
        "growth": Principal(
            "agent_growth", "growth-intelligence-analyst", brand_id
        ),
    }

    learning_context = finalize_record(
        {
            "schema_version": "1.0",
            "artifact_type": "learning_context_manifest",
            "context_manifest_id": "learning_context_guide_v1",
            "brand_id": brand_id,
            "paperclip_issue_id": issue_id,
            "query": {
                "product": "search_core",
                "workflow": "fictional_article",
                "task_class": "vertical_slice",
            },
            "record_bindings": [],
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    store.put(principals["director"], learning_context)
    records["learning_context"] = learning_context

    brand = make_envelope(
        artifact_type="brand_profile",
        artifact_id="profile_lantern_v1",
        brand_id=brand_id,
        campaign_id=campaign_id,
        asset_id=asset_id,
        issue_id=issue_id,
        created_by={"actor_type": "agent", "actor_id": "agent_steward"},
        payload={
            "brand_name": "Lantern Garden Co.",
            "approved_claims": ["The guide contains five fictional checklist steps."],
            "prohibited_claims": ["guaranteed results"],
        },
        status="approved",
    )
    store.put(principals["steward"], brand)
    records["brand"] = brand

    brief = make_envelope(
        artifact_type="content_brief",
        artifact_id="brief_guide_v1",
        brand_id=brand_id,
        campaign_id=campaign_id,
        asset_id=asset_id,
        issue_id=issue_id,
        created_by={"actor_type": "agent", "actor_id": "agent_strategist"},
        source_artifact_ids=[brand["artifact_id"]],
        payload={
            "objective": "Help fictional apartment gardeners choose a first project.",
            "audience": "Apartment gardeners",
            "information_gain": "A five-step decision checklist.",
            "cta": "Download the fictional worksheet.",
        },
        status="approved",
    )
    store.put(principals["strategist"], brief)
    records["brief"] = brief

    public_fields = {
        "title": "Five checks before starting a balcony garden",
        "body": [
            "Check the light, wind, space, water access, and building rules.",
            "Use the fictional worksheet to record each answer.",
        ],
        "cta": "Download the fictional worksheet.",
    }
    draft = make_envelope(
        artifact_type="draft_asset_package",
        artifact_id="draft_guide_v1",
        brand_id=brand_id,
        campaign_id=campaign_id,
        asset_id=asset_id,
        issue_id=issue_id,
        created_by={"actor_type": "agent", "actor_id": "agent_producer"},
        source_artifact_ids=[brief["artifact_id"]],
        payload={
            "public_fields": public_fields,
            "internal_notes": ["Fictional fixture; no real client or claim."],
            "claim_register": [
                {
                    "claim_id": "claim_1",
                    "text": "The guide contains five checklist steps.",
                    "source_ref": brand["artifact_id"],
                }
            ],
            "source_register": [brand["artifact_id"]],
        },
    )
    store.put(principals["producer"], draft)
    records["draft"] = draft

    complete = make_envelope(
        artifact_type="complete_asset_package",
        artifact_id="complete_guide_v1",
        brand_id=brand_id,
        campaign_id=campaign_id,
        asset_id=asset_id,
        issue_id=issue_id,
        created_by={"actor_type": "agent", "actor_id": "agent_optimiser"},
        source_artifact_ids=[draft["artifact_id"]],
        payload={
            **draft["payload"],
            "metadata": {
                "title": public_fields["title"],
                "description": "A fictional five-step balcony garden checklist.",
            },
            "internal_links": [],
            "structured_data": None,
            "upstream_checksum": draft["content_checksum"],
        },
        status="review",
    )
    store.put(principals["optimiser"], complete)
    records["complete"] = complete

    verdict = make_envelope(
        artifact_type="qa_verdict",
        artifact_id="qa_guide_v1",
        brand_id=brand_id,
        campaign_id=campaign_id,
        asset_id=asset_id,
        issue_id=issue_id,
        created_by={"actor_type": "agent", "actor_id": "agent_qa"},
        source_artifact_ids=[complete["artifact_id"]],
        payload={
            "verdict": "PASS",
            "reviewed_checksum": complete["content_checksum"],
            "findings": [],
        },
        status="approved",
    )
    store.put(principals["qa"], verdict)
    records["qa_verdict"] = verdict

    qa_package = make_envelope(
        artifact_type="qa_passed_asset_package",
        artifact_id="qa_passed_guide_v1",
        brand_id=brand_id,
        campaign_id=campaign_id,
        asset_id=asset_id,
        issue_id=issue_id,
        created_by={"actor_type": "agent", "actor_id": "agent_qa"},
        source_artifact_ids=[complete["artifact_id"], verdict["artifact_id"]],
        payload={
            **complete["payload"],
            "qa_verdict_id": verdict["artifact_id"],
            "qa_verdict_checksum": verdict["content_checksum"],
            "reviewed_checksum": complete["content_checksum"],
        },
        status="approved",
    )
    store.put(principals["qa"], qa_package)
    records["qa_package"] = qa_package

    now = datetime.now(timezone.utc)
    window = {
        "starts_at": (now - timedelta(minutes=5)).isoformat(),
        "ends_at": (now + timedelta(hours=1)).isoformat(),
    }
    manifest = make_publication_manifest(
        manifest_id="manifest_guide_v1",
        qa_package=qa_package,
        destination_ref="mock_cms:lantern",
        environment="sandbox",
        operation="publish",
        schedule_window=window,
        transformation_version="mock-adapter/1",
    )
    store.put(principals["director"], manifest)
    records["manifest"] = manifest

    approval = make_approval_record(
        approval_id="approval_guide_v1",
        manifest=manifest,
        approver_id="human_owner",
        authority_role="brand_owner",
        decided_at=now.isoformat(),
        expires_at=(now + timedelta(minutes=30)).isoformat(),
    )
    store.put(principals["approver"], approval)
    records["approval"] = approval

    capability_registry = CapabilityRegistry()
    capability = make_capability_record(
        capability_id="cap_mock_publish",
        brand_id=brand_id,
        actor_id=principals["publisher"].actor_id,
        role_id=principals["publisher"].role_id,
        destination_ref="mock_cms:lantern",
        environment="sandbox",
        operation="publish",
        action_class="external_write",
        data_class="public_content",
        issued_by=principals["director"].actor_id,
        issued_at=(now - timedelta(minutes=5)).isoformat(),
        not_before=(now - timedelta(minutes=5)).isoformat(),
        expires_at=(now + timedelta(minutes=30)).isoformat(),
    )
    capability_registry.register(principals["director"], capability)
    publisher = MockPublisher()
    context = multiprocessing.get_context("spawn")
    authority_control, worker_control = context.Pipe(duplex=True)
    worker = context.Process(
        target=_fictional_publish_worker, args=(worker_control,), daemon=True
    )
    worker.start()
    worker_control.close()
    host = None
    try:
        enrollment = _authority_enrollment_for_process(
            worker.pid,
            principals["publisher"],
            runtime_id="runtime_agent_publisher",
        )
        host = _provision_authority_gateway_host(
            enrollment=enrollment,
            capability_id=capability["capability_id"],
            capability_registry=capability_registry,
            credential_broker=fictional_credential_broker(capability),
            publisher=publisher,
            approval_store=store,
            approval_authorities={brand_id: {"brand_owner": ["human_owner"]}},
            action_ledger=InMemoryActionLedger(),
        )
        authority_control.send(
            {
                "socket_path": host.socket_path,
                "manifest": manifest,
                "approval_id": approval["approval_id"],
                "idempotency_key": "idem_guide_v1",
            }
        )
        if not authority_control.poll(5):
            raise RuntimeError("fictional publisher worker did not respond")
        outcome = authority_control.recv()
        if outcome.get("outcome") != "ALLOW":
            raise GatewayDenied(str(outcome.get("code", "GATEWAY_HOST_DENIED")))
        snapshot = host.snapshot()
        authoritative_receipts = snapshot["authoritative_receipts"]
        if len(authoritative_receipts) != 1:
            raise GatewayDenied("AUTHORITATIVE_RECEIPT_MISSING")
        receipt = authoritative_receipts[0]
        publisher.calls = snapshot["publisher_calls"]
        publisher.objects = snapshot["publisher_objects"]
    finally:
        authority_control.close()
        worker.join(timeout=2)
        if worker.is_alive():
            worker.terminate()
            worker.join(timeout=2)
        if host is not None:
            host.close()
    store.put(principals["publisher"], receipt)
    records["receipt"] = receipt

    snapshot = finalize_record(
        {
            "schema_version": "1.0",
            "artifact_type": "performance_snapshot",
            "artifact_id": "performance_guide_v1",
            "brand_id": brand_id,
            "campaign_id": campaign_id,
            "asset_id": asset_id,
            "publication_receipt_id": receipt["receipt_id"],
            "source": "fictional_fixture",
            "observation_period": "immediate_validation",
            "metrics": {},
            "conclusion_class": "insufficient_evidence",
            "diagnosis": "Publication integrity passed; outcome data is not available.",
        }
    )
    store.put(principals["growth"], snapshot)
    records["performance"] = snapshot

    candidate = finalize_record(
        {
            "schema_version": "1.0",
            "artifact_type": "candidate_learning",
            "candidate_learning_id": "candidate_guide_v1",
            "brand_id": brand_id,
            "proposed_by_role": "growth-intelligence-analyst",
            "failure_observation_ids": [],
            "evidence_refs": [snapshot["artifact_id"]],
            "proposed_correction": (
                "Treat immediate publication validation as insufficient evidence "
                "of business impact."
            ),
            "confidence": 1.0,
            "proposed_reuse_scope": "brand-only",
            "authority": "proposal_only",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    store.put(principals["growth"], candidate)
    records["candidate_learning"] = candidate

    learning_record = finalize_record(
        {
            "schema_version": "1.0",
            "artifact_type": "learning_record",
            "learning_record_id": "learning_guide_v1",
            "version": 1,
            "brand_id": brand_id,
            "validation_status": "validated",
            "lifecycle_status": "active",
            "reuse_scope": "brand-only",
            "expected_result": "Immediate validation proves delivery integrity only.",
            "actual_result": "No business-outcome observation was available.",
            "attempted_approach": "Inspect the immediate fictional snapshot.",
            "validated_correction": candidate["proposed_correction"],
            "evidence_refs": [snapshot["artifact_id"], candidate["candidate_learning_id"]],
            "confidence": 1.0,
            "limitations": ["Fictional fixture; no real outcome data."],
            "fresh_until": (now + timedelta(days=30)).isoformat(),
            "reviewed_at": now.isoformat(),
            "supersedes": None,
            "dispositioned_by": "agent_director",
        }
    )
    store.put(principals["director"], learning_record)
    records["learning_record"] = learning_record
    return VerticalSliceResult(store=store, publisher=publisher, records=records)
