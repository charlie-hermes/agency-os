"""Repeatable, evidence-bound Fleet AI Market Observatory baseline."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .brand_intelligence import BrandIntelligenceAuthority, make_generation2_record, source_reference
from .contracts import ContractError
from .store import Principal


def load_search_evidence(path: Path) -> dict[str, Any]:
    evidence = json.loads(path.read_text(encoding="utf-8"))
    if (
        evidence.get("artifact_type") != "permitted_public_search_snapshot"
        or evidence.get("brand_id") != "brand_fleet"
        or evidence.get("external_ai_coverage") != "unknown"
    ):
        raise ContractError("Fleet Observatory evidence identity or scope is invalid")
    if len(evidence.get("queries", [])) != 20 or len(evidence.get("results", [])) != 20:
        raise ContractError("Fleet Observatory baseline must contain exactly 20 query results")
    runs = evidence.get("capture_runs", [])
    if len(runs) != 2 or any(item.get("query_count") != 20 for item in runs):
        raise ContractError("Fleet Observatory requires two complete 20-query captures")
    if any(item.get("exact_domain_result_count") != 0 for item in evidence["results"]):
        raise ContractError("Fleet baseline result interpretation differs from result rows")
    return evidence


def build_observatory_records(
    authority: BrandIntelligenceAuthority,
    *,
    evidence_path: Path,
    repository_root: Path,
    director: Principal,
    analyst: Principal,
    paperclip_issue_id: str,
) -> dict[str, Any]:
    evidence = load_search_evidence(evidence_path)
    launch = authority.active_missions(analyst, launch_only=True)
    queries = evidence["queries"]
    if len(launch) != 20 or [item["variants"][0] for item in launch] != queries:
        raise ContractError("approved search evidence does not match the 20 launch mission variants")
    relative = evidence_path.resolve().relative_to(repository_root.resolve())
    locator = f"repo://{relative.as_posix()}"
    material = {
        "source_id": "material_fleet_public_search_baseline",
        "source_version": 1,
        "source_checksum": "sha256:" + hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        "locator": locator,
    }
    source = make_generation2_record(
        "brand_source", brand_id=director.brand_id, created_by=director.actor_id,
        provenance=[material], created_at=evidence["captured_at"], effective_at=evidence["captured_at"],
        source_id="source_fleet_public_search_baseline", source_class="permitted_public_search_snapshot",
        authority="approved_observation_evidence", locator=locator, status="active",
        owner_id=director.actor_id, observed_at=evidence["captured_at"], expires_at=None,
    )
    reference = source_reference(source)
    runs: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    for capture in evidence["capture_runs"]:
        run = make_generation2_record(
            "observation_run", brand_id=analyst.brand_id, created_by=analyst.actor_id,
            provenance=[reference], created_at=capture["completed_at"], effective_at=capture["completed_at"],
            run_id=capture["run_id"], mission_ids=[item["mission_id"] for item in launch],
            adapter=evidence["adapter"], adapter_version=evidence["adapter_version"],
            model="public_search_index", model_version="not_disclosed",
            started_at=capture["completed_at"], completed_at=capture["completed_at"], status="complete",
        )
        runs.append(run)
        run_number = capture["run_id"].rsplit("_", 1)[-1]
        for position, mission in enumerate(launch, start=1):
            observations.append(
                make_generation2_record(
                    "observation", brand_id=analyst.brand_id, created_by=analyst.actor_id,
                    provenance=[reference], created_at=capture["completed_at"], effective_at=capture["completed_at"],
                    observation_id=f"observation_fleet_{run_number}_{position:03d}",
                    run_id=run["run_id"], mission_id=mission["mission_id"],
                    variant=mission["variants"][0],
                    response="No exact madebyfleet.com result was returned for this query in the approved capture.",
                    citations=[],
                    evaluations={
                        "exact_domain_result_count": 0,
                        "fleet_domain_mentioned": False,
                        "scope": "permitted_public_search_only",
                        "external_ai_coverage": "unknown",
                    },
                    observed_at=capture["completed_at"], status="active",
                )
            )
    finding = make_generation2_record(
        "market_finding", brand_id=analyst.brand_id, created_by=analyst.actor_id,
        provenance=[reference], created_at=evidence["captured_at"], effective_at=evidence["captured_at"],
        finding_id="finding_fleet_public_explainer_gap_v1",
        mission_ids=[item["mission_id"] for item in launch],
        observation_refs=[item["observation_id"] for item in observations],
        finding="The approved two-run public-search baseline returned no exact madebyfleet.com result across the 20 launch missions.",
        classification="content_gap", confidence=1.0,
        knowns=evidence["knowns"], inferences=evidence["inferences"], unknowns=evidence["unknowns"],
        paperclip_issue_id=paperclip_issue_id, status="active",
    )
    return {"source": source, "runs": runs, "observations": observations, "finding": finding}


def run_fleet_observatory(
    authority: BrandIntelligenceAuthority,
    *,
    evidence_path: Path,
    repository_root: Path,
    director: Principal,
    analyst: Principal,
    reviewer: Principal,
    paperclip_issue_id: str,
) -> dict[str, Any]:
    records = build_observatory_records(
        authority, evidence_path=evidence_path, repository_root=repository_root,
        director=director, analyst=analyst, paperclip_issue_id=paperclip_issue_id,
    )
    authority.put(director, records["source"])
    for run in records["runs"]:
        authority.put(analyst, run)
    for observation in records["observations"]:
        authority.put(analyst, observation)
    authority.admit_finding(analyst, records["finding"])
    summary = authority.observatory_summary(reviewer)
    if (
        summary["complete_run_count"] < 2
        or summary["observation_count"] < 40
        or summary["finding_count"] < 1
        or summary["external_ai_coverage"] != "unknown"
    ):
        raise ContractError("Fleet Observatory acceptance evidence is incomplete")
    return {
        "status": "pass", "brand_id": analyst.brand_id,
        "launch_mission_count": 20, "complete_run_count": summary["complete_run_count"],
        "observation_count": summary["observation_count"], "finding_count": summary["finding_count"],
        "finding_id": records["finding"]["finding_id"],
        "finding_checksum": records["finding"]["content_checksum"],
        "knowns": records["finding"]["knowns"],
        "inferences": records["finding"]["inferences"],
        "unknowns": records["finding"]["unknowns"],
        "external_ai_coverage": "unknown",
    }
