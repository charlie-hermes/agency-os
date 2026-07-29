"""Fleet's approved Brand Twin and customer-mission production data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .brand_intelligence import (
    ApprovalBinding,
    BrandIntelligenceAuthority,
    make_generation2_record,
    source_reference,
)
from .contracts import ContractError, canonical_checksum
from .store import Principal


def load_fleet_brand_config(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema_version") != "2.0" or value.get("brand_id") != "brand_fleet":
        raise ValueError("Fleet Brand Intelligence config identity is invalid")
    if len(value.get("sources", [])) < 3 or len(value.get("claims", [])) < 1:
        raise ValueError("Fleet Brand Intelligence config is incomplete")
    groups = value.get("mission_groups", [])
    if sum(len(group.get("intents", [])) for group in groups) != 50:
        raise ValueError("Fleet must define exactly 50 initial customer missions")
    return value


def build_fleet_records(config: Mapping[str, Any], repository_root: Path) -> dict[str, Any]:
    """Build deterministic records without changing authority state."""

    brand_id = str(config["brand_id"])
    created_at = str(config["created_at"])
    actors = config["actors"]
    director = str(actors["director"])
    analyst = str(actors["analyst"])
    sources: list[dict[str, Any]] = []
    source_refs: dict[str, dict[str, Any]] = {}
    for item in config["sources"]:
        relative = str(item["locator"]).removeprefix("repo://")
        source_path = (repository_root / relative).resolve()
        if repository_root.resolve() not in source_path.parents or not source_path.is_file():
            raise ValueError(f"approved Fleet source is unavailable: {relative}")
        material_ref = {
            "source_id": f"material_{item['source_id']}",
            "source_version": 1,
            "source_checksum": "sha256:" + __import__("hashlib").sha256(source_path.read_bytes()).hexdigest(),
            "locator": str(item["locator"]),
        }
        source = make_generation2_record(
            "brand_source", brand_id=brand_id, created_by=director,
            provenance=[material_ref], created_at=created_at, effective_at=created_at,
            source_id=item["source_id"], source_class=item["source_class"],
            authority=item["authority"], locator=item["locator"], status="active",
            owner_id=director, observed_at=created_at, expires_at=None,
        )
        sources.append(source)
        source_refs[source["source_id"]] = source_reference(source)

    plan_ref = source_refs["source_fleet_enterprise_plan"]
    entities = [
        make_generation2_record(
            "brand_entity", brand_id=brand_id, created_by=director,
            provenance=[plan_ref], created_at=created_at, effective_at=created_at,
            entity_id=entity_id, entity_type=entity_type, canonical_name=name,
            aliases=aliases, status="active", evidence_refs=[plan_ref],
        )
        for entity_id, entity_type, name, aliases in config["entities"]
    ]

    claims: list[dict[str, Any]] = []
    claim_evidence: list[dict[str, Any]] = []
    for item in config["claims"]:
        reference = source_refs[item["source_id"]]
        claim = make_generation2_record(
            "brand_claim", brand_id=brand_id, created_by=director,
            provenance=[reference], created_at=created_at, effective_at=created_at,
            claim_id=item["claim_id"], subject_entity_id=item["subject_entity_id"],
            predicate=item["predicate"], object=item["object"], status="active",
            approval_state="approved", owner_id=director, evidence_refs=[reference],
            valid_from=created_at, valid_until=None,
        )
        claims.append(claim)
        claim_evidence.append(
            make_generation2_record(
                "claim_evidence", brand_id=brand_id, created_by=director,
                provenance=[reference], created_at=created_at, effective_at=created_at,
                evidence_id=f"evidence_{item['claim_id']}", claim_id=item["claim_id"],
                source_ref=reference, extract=item["extract"], confidence=1.0,
                observed_at=created_at, status="active",
            )
        )

    policy_ref = source_refs["source_fleet_generation2_decisions"]
    policies = [
        make_generation2_record(
            "brand_policy", brand_id=brand_id, created_by=director,
            provenance=[policy_ref], created_at=created_at, effective_at=created_at,
            policy_id=policy_id, policy_type=policy_type, rule=rule,
            status="active", owner_id=director, approval_ref="paperclip_brand_twin_claim_bundle",
        )
        for policy_id, policy_type, rule in config["policies"]
    ]

    missions: list[dict[str, Any]] = []
    position = 0
    for group in config["mission_groups"]:
        for intent in group["intents"]:
            position += 1
            launch_priority = position <= 20
            if launch_priority:
                variants = [
                    f'"madebyfleet.com" {intent}',
                    f'"Fleet" {intent}',
                ]
            else:
                variants = [
                    f"{intent.capitalize()} for a modern brand",
                    f"Which platform can help a brand {intent}?",
                ]
            missions.append(
                make_generation2_record(
                    "customer_mission", brand_id=brand_id, created_by=analyst,
                    provenance=[plan_ref], created_at=created_at, effective_at=created_at,
                    mission_id=f"mission_fleet_{position:03d}", audience=group["audience"],
                    intent=intent,
                    success_definition={
                        "launch_priority": launch_priority,
                        "evidence_standard": "two complete versioned runs",
                        "desired_answer": "A factual, appropriately cited explanation that may include Fleet when supported.",
                    },
                    variants=variants, status="active",
                )
            )

    return {
        "sources": sources, "entities": entities, "claims": claims,
        "claim_evidence": claim_evidence, "policies": policies, "missions": missions,
    }


def claim_approval_package(records: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "2.0",
        "artifact_type": "brand_twin_claim_approval_package",
        "brand_id": "brand_fleet",
        "claims": [
            {"claim_id": item["claim_id"], "checksum": item["content_checksum"]}
            for item in records["claims"]
        ],
        "policies": [
            {"policy_id": item["policy_id"], "checksum": item["content_checksum"]}
            for item in records["policies"]
        ],
    }


def approval_binding_from_paperclip(
    approval: Mapping[str, Any], expected_package: Mapping[str, Any], *, approved_by: str,
) -> ApprovalBinding:
    if approval.get("status") != "approved" or approval.get("payload") != dict(expected_package):
        raise ContractError("Paperclip did not approve the exact Fleet Brand Twin package")
    approval_id = str(approval.get("id", ""))
    approved_at = str(approval.get("updatedAt") or approval.get("decidedAt") or "")
    if not approval_id or not approved_at:
        raise ContractError("Paperclip approval identity or decision time is missing")
    return ApprovalBinding(
        paperclip_approval_id=approval_id,
        approval_checksum=canonical_checksum(approval),
        approved_by=approved_by,
        approved_at=approved_at,
    )


def initialise_fleet_brand_intelligence(
    config: Mapping[str, Any], repository_root: Path, database_path: Path,
    paperclip_approval: Mapping[str, Any], *, approved_by: str,
) -> dict[str, Any]:
    records = build_fleet_records(config, repository_root)
    package = claim_approval_package(records)
    binding = approval_binding_from_paperclip(
        paperclip_approval, package, approved_by=approved_by,
    )
    authority = BrandIntelligenceAuthority(database_path)
    actors = config["actors"]
    director = Principal(actors["director"], "agency-director", config["brand_id"])
    analyst = Principal(actors["analyst"], "growth-intelligence-analyst", config["brand_id"])
    reviewer = Principal("fleet-platform-assurance-reviewer", "platform-assurance-reviewer", config["brand_id"])
    for record in records["sources"] + records["entities"]:
        authority.put(director, record)
    for record in records["claims"]:
        authority.put(director, record, approval=binding)
    for record in records["claim_evidence"] + records["policies"]:
        authority.put(director, record)
    for record in records["missions"]:
        authority.put(analyst, record)
    profile = authority.operating_profile(reviewer, at=str(config["created_at"]))
    grounding = authority.content_grounding(reviewer, at=str(config["created_at"]))
    launch = authority.active_missions(reviewer, launch_only=True)
    if profile["conflicts"] or profile["evidence_gaps"]:
        raise ContractError("Fleet Brand Twin contains unresolved conflicts or evidence gaps")
    if len(profile["claims"]) != len(records["claims"]) or len(launch) != 20:
        raise ContractError("Fleet Brand Twin or launch mission catalogue is incomplete")
    return {
        "status": "pass", "brand_id": config["brand_id"],
        "source_count": len(profile["sources"]), "entity_count": len(profile["entities"]),
        "approved_claim_count": len(profile["claims"]), "policy_count": len(profile["policies"]),
        "mission_count": len(authority.active_missions(reviewer)), "launch_mission_count": len(launch),
        "profile_checksum": profile["content_checksum"],
        "grounding_checksum": grounding["content_checksum"],
        "paperclip_approval_id": binding.paperclip_approval_id,
        "approval_package_checksum": canonical_checksum(package),
    }
