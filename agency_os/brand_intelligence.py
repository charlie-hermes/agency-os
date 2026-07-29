"""Durable Fleet Brand Twin, mission, Observatory, and learning authority.

Paperclip remains authoritative for work and approvals.  This module owns the
brand-scoped, immutable evidence graph and produces read-only grounding for the
existing Content Engine.
"""

from __future__ import annotations

import copy
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping
from uuid import UUID

from .contracts import (
    ContractError,
    canonical_bytes,
    finalize_record,
    parse_time,
    utc_now,
    verify_record,
)
from .sqlite_storage import SQLiteStorageError, prepare_sqlite_storage, validate_sqlite_storage
from .store import Principal


class BrandIntelligenceError(RuntimeError):
    """The Brand Intelligence authority failed closed."""


class BrandIntelligenceAuthorizationError(PermissionError):
    """The caller is outside the admitted brand or role boundary."""


@dataclass(frozen=True)
class ApprovalBinding:
    """Paperclip evidence for one approved immutable record version."""

    paperclip_approval_id: str
    approval_checksum: str
    approved_by: str
    approved_at: str


_COMMON = frozenset(
    {
        "schema_version", "artifact_type", "brand_id", "created_at",
        "effective_at", "created_by", "provenance", "content_checksum", "version",
    }
)
_FIELDS: dict[str, frozenset[str]] = {
    "brand_source": _COMMON | {
        "source_id", "source_class", "authority", "locator", "status", "owner_id",
        "observed_at", "expires_at",
    },
    "brand_entity": _COMMON | {
        "entity_id", "entity_type", "canonical_name", "aliases", "status",
        "evidence_refs",
    },
    "brand_claim": _COMMON | {
        "claim_id", "subject_entity_id", "predicate", "object", "status",
        "approval_state", "owner_id", "evidence_refs", "valid_from", "valid_until",
    },
    "claim_evidence": _COMMON | {
        "evidence_id", "claim_id", "source_ref", "extract", "confidence",
        "observed_at", "status",
    },
    "brand_policy": _COMMON | {
        "policy_id", "policy_type", "rule", "status", "owner_id", "approval_ref",
    },
    "customer_mission": _COMMON | {
        "mission_id", "audience", "intent", "success_definition", "variants", "status",
    },
    "observation_run": _COMMON | {
        "run_id", "mission_ids", "adapter", "adapter_version", "model",
        "model_version", "started_at", "completed_at", "status",
    },
    "observation": _COMMON | {
        "observation_id", "run_id", "mission_id", "variant", "response", "citations",
        "evaluations", "observed_at", "status",
    },
    "market_finding": _COMMON | {
        "finding_id", "mission_ids", "observation_refs", "finding", "classification",
        "confidence", "knowns", "inferences", "unknowns", "paperclip_issue_id", "status",
    },
    "remediation_proposal": _COMMON | {
        "proposal_id", "finding_id", "proposed_change", "expected_effect", "risk",
        "approval_state", "paperclip_issue_id", "status",
    },
    "experiment": _COMMON | {
        "experiment_id", "proposal_id", "hypothesis", "baseline_refs", "treatment",
        "evaluation_plan", "status",
    },
    "outcome_event": _COMMON | {
        "outcome_id", "experiment_id", "metric", "value", "unit", "source_ref",
        "observed_at", "causal_status", "status",
    },
}
_ID_FIELD = {
    "brand_source": "source_id", "brand_entity": "entity_id",
    "brand_claim": "claim_id", "claim_evidence": "evidence_id",
    "brand_policy": "policy_id", "customer_mission": "mission_id",
    "observation_run": "run_id", "observation": "observation_id",
    "market_finding": "finding_id", "remediation_proposal": "proposal_id",
    "experiment": "experiment_id", "outcome_event": "outcome_id",
}
_WRITES = {
    "agency-director": frozenset(_FIELDS),
    "brand-brief-steward": frozenset(
        {"brand_source", "brand_entity", "brand_claim", "claim_evidence", "brand_policy"}
    ),
    "growth-intelligence-analyst": frozenset(
        {"customer_mission", "observation_run", "observation", "market_finding",
         "experiment", "outcome_event"}
    ),
}
_READERS = frozenset(
    {"agency-director", "platform-assurance-reviewer", "brand-brief-steward",
     "search-content-strategist", "content-producer", "editorial-integrity-qa",
     "growth-intelligence-analyst"}
)
_APPROVAL_TYPES = frozenset({"brand_claim", "remediation_proposal"})


def make_generation2_record(
    artifact_type: str,
    *,
    brand_id: str,
    created_by: str,
    provenance: Iterable[Mapping[str, Any]],
    created_at: str | None = None,
    effective_at: str | None = None,
    version: int = 1,
    **fields: Any,
) -> dict[str, Any]:
    """Construct one strict, checksummed Generation 2 record."""

    moment = created_at or utc_now()
    record = {
        "schema_version": "2.0",
        "artifact_type": artifact_type,
        "brand_id": brand_id,
        "created_at": moment,
        "effective_at": effective_at or moment,
        "created_by": created_by,
        "provenance": [copy.deepcopy(dict(item)) for item in provenance],
        "version": version,
        **copy.deepcopy(fields),
    }
    final = finalize_record(record)
    _validate_record(final)
    return final


def source_reference(record: Mapping[str, Any]) -> dict[str, Any]:
    if record.get("artifact_type") != "brand_source":
        raise ContractError("evidence references must point to a brand source")
    verify_record(record)
    return {
        "source_id": record["source_id"],
        "source_version": record["version"],
        "source_checksum": record["content_checksum"],
        "locator": record["locator"],
    }


class BrandIntelligenceAuthority:
    """Protected SQLite evidence graph shared by Fleet processes."""

    def __init__(self, database_path: str | os.PathLike[str], *, timeout_seconds: float = 5.0) -> None:
        self.database_path = Path(database_path)
        if str(database_path) == ":memory:":
            raise ValueError("BrandIntelligenceAuthority requires a durable file path")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.timeout_seconds = timeout_seconds
        try:
            self._storage_identity = prepare_sqlite_storage(self.database_path)
        except SQLiteStorageError as exc:
            raise BrandIntelligenceError("unsafe Brand Intelligence storage") from exc
        self._initialize()

    def put(
        self,
        principal: Principal,
        record: Mapping[str, Any],
        *,
        approval: ApprovalBinding | None = None,
    ) -> str:
        artifact_type = str(record.get("artifact_type", ""))
        record_id = str(record.get(_ID_FIELD.get(artifact_type, ""), "invalid_record"))
        self._authorize_write(principal, artifact_type, record_id, record)
        try:
            _validate_record(record)
            self._validate_approval_requirement(record, approval)
        except ContractError:
            self._audit(principal, "put", record_id, "DENY_CONTRACT")
            raise
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._validate_references(connection, principal.brand_id, record)
            existing = connection.execute(
                "SELECT record_json FROM records WHERE brand_id=? AND artifact_type=? AND record_id=? AND version=?",
                (principal.brand_id, artifact_type, record_id, record["version"]),
            ).fetchone()
            if existing is not None:
                if json.loads(existing[0]) != dict(record):
                    raise ContractError(f"{artifact_type} {record_id!r} version is immutable")
                self._validate_existing_approval(connection, record, approval)
                self._insert_audit(connection, principal, "put", record_id, "ALLOW_IDEMPOTENT")
                connection.commit()
                return record_id
            latest = connection.execute(
                "SELECT MAX(version) FROM records WHERE brand_id=? AND artifact_type=? AND record_id=?",
                (principal.brand_id, artifact_type, record_id),
            ).fetchone()[0]
            if latest is not None and int(record["version"]) != int(latest) + 1:
                raise ContractError("record versions must be contiguous")
            connection.execute(
                "INSERT INTO records(brand_id,artifact_type,record_id,version,status,checksum,record_json) VALUES(?,?,?,?,?,?,?)",
                (principal.brand_id, artifact_type, record_id, record["version"],
                 record["status"], record["content_checksum"],
                 canonical_bytes(record).decode("utf-8")),
            )
            if approval is not None:
                connection.execute(
                    "INSERT INTO record_approvals(brand_id,artifact_type,record_id,version,paperclip_approval_id,approval_checksum,approved_by,approved_at) VALUES(?,?,?,?,?,?,?,?)",
                    (principal.brand_id, artifact_type, record_id, record["version"],
                     approval.paperclip_approval_id, approval.approval_checksum,
                     approval.approved_by, approval.approved_at),
                )
            self._insert_audit(connection, principal, "put", record_id, "ALLOW")
            connection.commit()
        except (ContractError, BrandIntelligenceAuthorizationError):
            connection.rollback()
            self._audit(principal, "put", record_id, "DENY_WRITE")
            raise
        except (sqlite3.Error, json.JSONDecodeError, TypeError, ValueError) as exc:
            connection.rollback()
            raise BrandIntelligenceError("could not store Brand Intelligence record") from exc
        finally:
            connection.close()
        return record_id

    def get(self, principal: Principal, artifact_type: str, record_id: str, *, version: int | None = None) -> dict[str, Any]:
        self._authorize_read(principal)
        query = "SELECT record_json FROM records WHERE brand_id=? AND artifact_type=? AND record_id=?"
        parameters: tuple[Any, ...] = (principal.brand_id, artifact_type, record_id)
        if version is None:
            query += " ORDER BY version DESC LIMIT 1"
        else:
            query += " AND version=?"
            parameters += (version,)
        row = self._fetch_one(query, parameters)
        if row is None:
            self._audit(principal, "get", record_id, "NOT_FOUND_OR_WRONG_TENANT")
            raise KeyError(record_id)
        record = json.loads(row[0])
        _validate_record(record)
        if record["brand_id"] != principal.brand_id:
            raise BrandIntelligenceError("stored tenant key does not match record")
        self._audit(principal, "get", record_id, "ALLOW")
        return record

    def admit_finding(
        self,
        principal: Principal,
        record: Mapping[str, Any],
    ) -> str:
        if record.get("artifact_type") != "market_finding":
            raise ContractError("admit_finding requires a market_finding")
        observations = [
            self.get(principal, "observation", str(reference))
            for reference in record.get("observation_refs", [])
        ]
        if not observations:
            raise ContractError("a finding requires observations")
        mission_runs: dict[str, set[str]] = {str(item): set() for item in record["mission_ids"]}
        for observation in observations:
            mission_id = observation["mission_id"]
            if mission_id not in mission_runs:
                raise ContractError("finding observation has an unrelated mission")
            run = self.get(principal, "observation_run", observation["run_id"])
            if run["status"] != "complete":
                raise ContractError("finding observations require complete runs")
            mission_runs[mission_id].add(run["run_id"])
        if any(len(run_ids) < 2 for run_ids in mission_runs.values()):
            raise ContractError("every finding mission requires evidence from two runs")
        return self.put(principal, record)

    def operating_profile(
        self,
        principal: Principal,
        *,
        at: str | None = None,
    ) -> dict[str, Any]:
        self._authorize_read(principal)
        moment = parse_time(at or utc_now())
        sources = self._latest(principal.brand_id, "brand_source")
        source_map = {
            item["source_id"]: item for item in sources
            if item["status"] == "active" and _active_window(item, moment, "observed_at", "expires_at")
        }
        entities = [item for item in self._latest(principal.brand_id, "brand_entity") if item["status"] == "active"]
        evidence = self._latest(principal.brand_id, "claim_evidence")
        evidence_by_claim: dict[str, list[dict[str, Any]]] = {}
        for item in evidence:
            if item["status"] == "active" and _reference_is_active(item["source_ref"], source_map):
                evidence_by_claim.setdefault(item["claim_id"], []).append(item)
        candidates: list[dict[str, Any]] = []
        gaps: list[dict[str, str]] = []
        for claim in self._latest(principal.brand_id, "brand_claim"):
            reason = self._claim_exclusion_reason(claim, source_map, evidence_by_claim, moment)
            if reason is None:
                candidates.append(claim)
            else:
                gaps.append({"claim_id": claim["claim_id"], "reason": reason})
        grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for claim in candidates:
            grouped.setdefault((claim["subject_entity_id"], claim["predicate"]), []).append(claim)
        conflicts: list[dict[str, Any]] = []
        admitted: list[dict[str, Any]] = []
        for key, claims in grouped.items():
            values = {canonical_bytes(item["object"]) for item in claims}
            if len(values) > 1:
                conflicts.append({"subject_entity_id": key[0], "predicate": key[1],
                                  "claim_ids": sorted(item["claim_id"] for item in claims)})
            else:
                admitted.extend(claims)
        policies = [item for item in self._latest(principal.brand_id, "brand_policy") if item["status"] == "active"]
        profile = {
            "schema_version": "2.0", "artifact_type": "brand_operating_profile",
            "profile_id": f"profile_{principal.brand_id.removeprefix('brand_')}",
            "brand_id": principal.brand_id, "generated_at": moment.astimezone(timezone.utc).isoformat(),
            "sources": [_safe_source(item) for item in source_map.values()],
            "entities": [_safe_entity(item) for item in entities],
            "claims": [_safe_claim(item, evidence_by_claim[item["claim_id"]]) for item in admitted],
            "policies": [_safe_policy(item) for item in policies],
            "evidence_gaps": sorted(gaps, key=lambda item: item["claim_id"]),
            "conflicts": sorted(conflicts, key=lambda item: (item["subject_entity_id"], item["predicate"])),
        }
        return finalize_record(profile)

    def content_grounding(
        self,
        principal: Principal,
        *,
        required_claim_ids: Iterable[str] = (),
        at: str | None = None,
    ) -> dict[str, Any]:
        profile = self.operating_profile(principal, at=at)
        claims = {item["claim_id"]: item for item in profile["claims"]}
        required = tuple(required_claim_ids)
        missing = sorted(set(required) - set(claims))
        if missing:
            raise BrandIntelligenceAuthorizationError(
                f"required approved claims are unavailable: {', '.join(missing)}"
            )
        selected = [claims[item] for item in required] if required else list(claims.values())
        return finalize_record(
            {
                "schema_version": "2.0", "artifact_type": "content_grounding",
                "brand_id": principal.brand_id, "profile_checksum": profile["content_checksum"],
                "claim_ids": [item["claim_id"] for item in selected],
                "claims": selected, "policies": profile["policies"],
                "conflicts": profile["conflicts"], "evidence_gaps": profile["evidence_gaps"],
            }
        )

    def active_missions(self, principal: Principal, *, launch_only: bool = False) -> list[dict[str, Any]]:
        self._authorize_read(principal)
        missions = [item for item in self._latest(principal.brand_id, "customer_mission") if item["status"] == "active"]
        if launch_only:
            missions = [item for item in missions if item["success_definition"].get("launch_priority") is True]
        return missions

    def observatory_summary(self, principal: Principal) -> dict[str, Any]:
        self._authorize_read(principal)
        runs = self._latest(principal.brand_id, "observation_run")
        observations = self._latest(principal.brand_id, "observation")
        findings = self._latest(principal.brand_id, "market_finding")
        return finalize_record(
            {
                "schema_version": "2.0", "artifact_type": "observatory_summary",
                "brand_id": principal.brand_id,
                "complete_run_count": sum(item["status"] == "complete" for item in runs),
                "observation_count": sum(item["status"] == "active" for item in observations),
                "finding_count": sum(item["status"] == "active" for item in findings),
                "adapters": sorted({item["adapter"] for item in runs}),
                "external_ai_coverage": "unknown",
                "limitations": [
                    "The current baseline is approved public-search evidence, not a universal AI ranking.",
                    "A missing result does not prove absence from every model or interface.",
                ],
            }
        )

    def audit_events(self, principal: Principal) -> list[dict[str, Any]]:
        self._authorize_read(principal)
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT sequence,actor_id,role_id,brand_id,operation,target_id,outcome,recorded_at FROM authority_audit WHERE brand_id=? ORDER BY sequence",
                (principal.brand_id,),
            ).fetchall()
        finally:
            connection.close()
        return [
            {"sequence": row[0], "actor_id": row[1], "role_id": row[2], "brand_id": row[3],
             "operation": row[4], "target_id": row[5], "outcome": row[6], "recorded_at": row[7]}
            for row in rows
        ]

    def schema_version(self) -> int:
        row = self._fetch_one("SELECT version FROM schema_metadata WHERE id=1", ())
        if row is None:
            raise BrandIntelligenceError("Brand Intelligence schema metadata is missing")
        return int(row[0])

    def _claim_exclusion_reason(
        self,
        claim: Mapping[str, Any],
        sources: Mapping[str, Mapping[str, Any]],
        evidence: Mapping[str, list[dict[str, Any]]],
        moment: datetime,
    ) -> str | None:
        if claim["status"] != "active":
            return "inactive"
        if claim["approval_state"] != "approved":
            return "not_approved"
        if not self._has_approval(claim["brand_id"], "brand_claim", claim["claim_id"], claim["version"]):
            return "paperclip_approval_missing"
        if not evidence.get(claim["claim_id"]):
            return "evidence_missing_or_stale"
        if not all(_reference_is_active(reference, sources) for reference in claim["evidence_refs"]):
            return "source_missing_or_stale"
        if not _active_window(claim, moment, "valid_from", "valid_until"):
            return "outside_validity_window"
        return None

    def _validate_approval_requirement(self, record: Mapping[str, Any], approval: ApprovalBinding | None) -> None:
        requires = (
            record["artifact_type"] == "brand_claim" and record["approval_state"] == "approved"
        ) or (
            record["artifact_type"] == "remediation_proposal" and
            record["approval_state"] in {"approved", "implemented"}
        )
        if requires and approval is None:
            raise ContractError("approved records require Paperclip approval evidence")
        if not requires and approval is not None:
            raise ContractError("approval evidence does not match record state")
        if approval is not None:
            if not all((approval.paperclip_approval_id, approval.approval_checksum,
                        approval.approved_by, approval.approved_at)):
                raise ContractError("Paperclip approval evidence is incomplete")
            try:
                UUID(approval.paperclip_approval_id)
            except (TypeError, ValueError) as exc:
                raise ContractError("Paperclip approval ID must be a UUID") from exc
            checksum = approval.approval_checksum
            if (
                not checksum.startswith("sha256:")
                or len(checksum) != 71
                or any(character not in "0123456789abcdef" for character in checksum[7:])
            ):
                raise ContractError("Paperclip approval checksum is invalid")
            parse_time(approval.approved_at)

    def _validate_existing_approval(self, connection: sqlite3.Connection, record: Mapping[str, Any], approval: ApprovalBinding | None) -> None:
        row = connection.execute(
            "SELECT paperclip_approval_id,approval_checksum,approved_by,approved_at FROM record_approvals WHERE brand_id=? AND artifact_type=? AND record_id=? AND version=?",
            (record["brand_id"], record["artifact_type"], record[_ID_FIELD[record["artifact_type"]]], record["version"]),
        ).fetchone()
        observed = None if row is None else ApprovalBinding(*row)
        if observed != approval:
            raise ContractError("stored Paperclip approval binding changed")

    def _validate_references(self, connection: sqlite3.Connection, brand_id: str, record: Mapping[str, Any]) -> None:
        if record["artifact_type"] != "brand_source":
            for reference in record["provenance"]:
                self._require_source_reference(connection, brand_id, reference)
        for reference in record.get("evidence_refs", []):
            self._require_source_reference(connection, brand_id, reference)
        if "source_ref" in record:
            self._require_source_reference(connection, brand_id, record["source_ref"])
        if record["artifact_type"] == "claim_evidence":
            self._require_record(connection, brand_id, "brand_claim", record["claim_id"])
        if record["artifact_type"] == "observation_run":
            for mission_id in record["mission_ids"]:
                self._require_record(connection, brand_id, "customer_mission", mission_id)
        if record["artifact_type"] == "observation":
            run = self._require_record(connection, brand_id, "observation_run", record["run_id"])
            mission = self._require_record(connection, brand_id, "customer_mission", record["mission_id"])
            if record["mission_id"] not in run["mission_ids"]:
                raise ContractError("observation mission is outside its run")
            if record["variant"] not in mission["variants"]:
                raise ContractError("observation variant is outside its versioned mission")
        if record["artifact_type"] == "market_finding":
            mission_runs = {mission_id: set() for mission_id in record["mission_ids"]}
            for observation_id in record["observation_refs"]:
                observation = self._require_record(
                    connection, brand_id, "observation", observation_id,
                )
                mission_id = observation["mission_id"]
                if mission_id not in mission_runs:
                    raise ContractError("finding observation has an unrelated mission")
                run = self._require_record(
                    connection, brand_id, "observation_run", observation["run_id"],
                )
                if run["status"] != "complete":
                    raise ContractError("finding observations require complete runs")
                mission_runs[mission_id].add(run["run_id"])
            if any(len(run_ids) < 2 for run_ids in mission_runs.values()):
                raise ContractError("every finding mission requires evidence from two runs")
        if record["artifact_type"] == "remediation_proposal":
            self._require_record(connection, brand_id, "market_finding", record["finding_id"])
        if record["artifact_type"] == "experiment":
            self._require_record(connection, brand_id, "remediation_proposal", record["proposal_id"])
        if record["artifact_type"] == "outcome_event":
            self._require_record(connection, brand_id, "experiment", record["experiment_id"])

    @staticmethod
    def _require_record(
        connection: sqlite3.Connection,
        brand_id: str,
        artifact_type: str,
        record_id: str,
    ) -> dict[str, Any]:
        row = connection.execute(
            "SELECT record_json FROM records WHERE brand_id=? AND artifact_type=? AND record_id=? ORDER BY version DESC LIMIT 1",
            (brand_id, artifact_type, record_id),
        ).fetchone()
        if row is None:
            raise ContractError(f"missing {artifact_type} reference: {record_id}")
        value = json.loads(row[0])
        _validate_record(value)
        return value

    @staticmethod
    def _require_source_reference(connection: sqlite3.Connection, brand_id: str, reference: Mapping[str, Any]) -> None:
        _validate_reference(reference)
        row = connection.execute(
            "SELECT checksum,record_json FROM records WHERE brand_id=? AND artifact_type='brand_source' AND record_id=? AND version=?",
            (brand_id, reference["source_id"], reference["source_version"]),
        ).fetchone()
        if row is None or row[0] != reference["source_checksum"]:
            raise ContractError("source evidence reference is missing or changed")
        stored = json.loads(row[1])
        if stored["locator"] != reference["locator"]:
            raise ContractError("source evidence locator changed")

    def _authorize_write(self, principal: Principal, artifact_type: str, target_id: str, record: Mapping[str, Any]) -> None:
        if artifact_type not in _WRITES.get(principal.role_id, frozenset()):
            self._audit(principal, "put", target_id, "DENY_ROLE")
            raise BrandIntelligenceAuthorizationError("role cannot write this Brand Intelligence record")
        if record.get("brand_id") != principal.brand_id:
            self._audit(principal, "put", target_id, "DENY_TENANT")
            raise BrandIntelligenceAuthorizationError("cross-tenant Brand Intelligence write denied")
        if record.get("created_by") != principal.actor_id:
            self._audit(principal, "put", target_id, "DENY_ACTOR")
            raise BrandIntelligenceAuthorizationError("record actor does not match principal")

    @staticmethod
    def _authorize_read(principal: Principal) -> None:
        if principal.role_id not in _READERS:
            raise BrandIntelligenceAuthorizationError("role cannot read Brand Intelligence")

    def _has_approval(self, brand_id: str, artifact_type: str, record_id: str, version: int) -> bool:
        return self._fetch_one(
            "SELECT 1 FROM record_approvals WHERE brand_id=? AND artifact_type=? AND record_id=? AND version=?",
            (brand_id, artifact_type, record_id, version),
        ) is not None

    def _latest(self, brand_id: str, artifact_type: str) -> list[dict[str, Any]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT r.record_json FROM records r JOIN (SELECT record_id,MAX(version) version FROM records WHERE brand_id=? AND artifact_type=? GROUP BY record_id) latest ON latest.record_id=r.record_id AND latest.version=r.version WHERE r.brand_id=? AND r.artifact_type=? ORDER BY r.record_id",
                (brand_id, artifact_type, brand_id, artifact_type),
            ).fetchall()
        finally:
            connection.close()
        records = [json.loads(row[0]) for row in rows]
        for record in records:
            _validate_record(record)
        return records

    def _initialize(self) -> None:
        connection = self._connect(validate_schema=False)
        try:
            journal = connection.execute("PRAGMA journal_mode=WAL").fetchone()
            if journal is None or str(journal[0]).lower() != "wal":
                raise BrandIntelligenceError("Brand Intelligence database did not enter WAL mode")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_metadata(id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, migrated_at TEXT NOT NULL);
                INSERT OR IGNORE INTO schema_metadata(id,version,migrated_at) VALUES(1,1,datetime('now'));
                CREATE TABLE IF NOT EXISTS records(
                  brand_id TEXT NOT NULL, artifact_type TEXT NOT NULL, record_id TEXT NOT NULL,
                  version INTEGER NOT NULL CHECK(version>0), status TEXT NOT NULL,
                  checksum TEXT NOT NULL, record_json TEXT NOT NULL,
                  PRIMARY KEY(brand_id,artifact_type,record_id,version));
                CREATE TABLE IF NOT EXISTS record_approvals(
                  brand_id TEXT NOT NULL, artifact_type TEXT NOT NULL, record_id TEXT NOT NULL,
                  version INTEGER NOT NULL, paperclip_approval_id TEXT NOT NULL,
                  approval_checksum TEXT NOT NULL, approved_by TEXT NOT NULL, approved_at TEXT NOT NULL,
                  PRIMARY KEY(brand_id,artifact_type,record_id,version),
                  FOREIGN KEY(brand_id,artifact_type,record_id,version)
                    REFERENCES records(brand_id,artifact_type,record_id,version));
                CREATE TABLE IF NOT EXISTS authority_audit(
                  sequence INTEGER PRIMARY KEY AUTOINCREMENT, actor_id TEXT NOT NULL,
                  role_id TEXT NOT NULL, brand_id TEXT NOT NULL, operation TEXT NOT NULL,
                  target_id TEXT NOT NULL, outcome TEXT NOT NULL, recorded_at TEXT NOT NULL);
                """
            )
            connection.commit()
        finally:
            connection.close()
        self._validate_schema()

    def _validate_schema(self) -> None:
        connection = self._connect(validate_schema=False)
        try:
            version = connection.execute("SELECT version FROM schema_metadata WHERE id=1").fetchone()
            integrity = connection.execute("PRAGMA quick_check").fetchone()
            if version is None or int(version[0]) != 1 or integrity is None or integrity[0] != "ok":
                raise BrandIntelligenceError("Brand Intelligence schema or integrity check failed")
        finally:
            connection.close()

    def _connect(self, *, validate_schema: bool = True) -> sqlite3.Connection:
        try:
            validate_sqlite_storage(self.database_path, self._storage_identity)
            connection = sqlite3.connect(self.database_path, timeout=self.timeout_seconds)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(f"PRAGMA busy_timeout={int(self.timeout_seconds * 1000)}")
            if validate_schema:
                row = connection.execute("SELECT version FROM schema_metadata WHERE id=1").fetchone()
                if row is None or int(row[0]) != 1:
                    raise BrandIntelligenceError("unsupported Brand Intelligence schema version")
            return connection
        except (SQLiteStorageError, sqlite3.Error) as exc:
            raise BrandIntelligenceError("could not open Brand Intelligence database safely") from exc

    def _fetch_one(self, query: str, parameters: tuple[Any, ...]) -> sqlite3.Row | tuple[Any, ...] | None:
        connection = self._connect()
        try:
            return connection.execute(query, parameters).fetchone()
        finally:
            connection.close()

    def _audit(self, principal: Principal, operation: str, target_id: str, outcome: str) -> None:
        try:
            connection = self._connect()
            self._insert_audit(connection, principal, operation, target_id, outcome)
            connection.commit()
        except (BrandIntelligenceError, sqlite3.Error):
            return
        finally:
            if "connection" in locals():
                connection.close()

    @staticmethod
    def _insert_audit(connection: sqlite3.Connection, principal: Principal, operation: str, target_id: str, outcome: str) -> None:
        connection.execute(
            "INSERT INTO authority_audit(actor_id,role_id,brand_id,operation,target_id,outcome,recorded_at) VALUES(?,?,?,?,?,?,?)",
            (principal.actor_id, principal.role_id, principal.brand_id, operation,
             target_id, outcome, utc_now()),
        )


def _validate_record(record: Mapping[str, Any]) -> None:
    artifact_type = str(record.get("artifact_type", ""))
    expected = _FIELDS.get(artifact_type)
    if expected is None:
        raise ContractError("unsupported Generation 2 artifact type")
    if frozenset(record) != expected:
        unknown = sorted(set(record) - expected)
        missing = sorted(expected - set(record))
        raise ContractError(f"strict fields failed; unknown={unknown}, missing={missing}")
    verify_record(record)
    if record["schema_version"] != "2.0" or not str(record["brand_id"]).startswith("brand_"):
        raise ContractError("invalid Generation 2 schema or brand")
    if not isinstance(record["version"], int) or isinstance(record["version"], bool) or record["version"] < 1:
        raise ContractError("record version must be a positive integer")
    if not isinstance(record["created_by"], str) or not record["created_by"]:
        raise ContractError("record creator is required")
    parse_time(record["created_at"])
    parse_time(record["effective_at"])
    if not isinstance(record["provenance"], list) or not record["provenance"]:
        raise ContractError("record provenance is required")
    for reference in record["provenance"]:
        _validate_reference(reference)
    record_id = record[_ID_FIELD[artifact_type]]
    if not isinstance(record_id, str) or not record_id:
        raise ContractError("record identifier is required")
    allowed_statuses = {
        "brand_source": {"active", "inactive", "superseded"},
        "brand_entity": {"active", "inactive", "superseded"},
        "brand_claim": {"active", "inactive", "disputed", "superseded"},
        "claim_evidence": {"active", "inactive", "superseded"},
        "brand_policy": {"active", "inactive", "superseded"},
        "customer_mission": {"active", "inactive", "superseded"},
        "observation": {"active", "inactive", "superseded"},
        "market_finding": {"active", "inactive", "disputed", "superseded"},
        "remediation_proposal": {"active", "inactive", "superseded"},
        "outcome_event": {"active", "inactive", "superseded"},
    }
    if artifact_type in allowed_statuses and record["status"] not in allowed_statuses[artifact_type]:
        raise ContractError("record status is invalid")
    if artifact_type == "brand_source":
        parse_time(record["observed_at"])
        if record["expires_at"] is not None:
            parse_time(record["expires_at"])
    if artifact_type == "brand_claim":
        if record["approval_state"] not in {"draft", "approved", "rejected"}:
            raise ContractError("claim approval state is invalid")
        for value in (record["valid_from"], record["valid_until"]):
            if value is not None:
                parse_time(value)
    if artifact_type == "claim_evidence":
        if not isinstance(record["confidence"], (int, float)) or isinstance(record["confidence"], bool) or not 0 <= record["confidence"] <= 1:
            raise ContractError("evidence confidence must be between zero and one")
    if artifact_type == "customer_mission":
        if not isinstance(record["variants"], list) or not record["variants"] or len(set(record["variants"])) != len(record["variants"]):
            raise ContractError("missions require unique variants")
    if artifact_type == "observation_run":
        if record["status"] not in {"running", "complete", "failed", "partial"}:
            raise ContractError("observation run status is invalid")
        if (
            not isinstance(record["mission_ids"], list)
            or not record["mission_ids"]
            or len(set(record["mission_ids"])) != len(record["mission_ids"])
        ):
            raise ContractError("observation runs require unique missions")
        parse_time(record["started_at"])
        if record["completed_at"] is not None:
            parse_time(record["completed_at"])
        if record["status"] == "complete" and record["completed_at"] is None:
            raise ContractError("complete observation runs require completed_at")
    if artifact_type == "market_finding":
        if not 0 <= record["confidence"] <= 1 or not record["observation_refs"]:
            raise ContractError("finding confidence or evidence is invalid")
    if artifact_type == "remediation_proposal":
        if record["approval_state"] not in {"proposed", "approved", "rejected", "implemented"}:
            raise ContractError("remediation approval state is invalid")
    if artifact_type == "experiment" and record["status"] not in {"planned", "running", "complete", "cancelled"}:
        raise ContractError("experiment status is invalid")


def _validate_reference(reference: Mapping[str, Any]) -> None:
    expected = {"source_id", "source_version", "source_checksum", "locator"}
    if set(reference) != expected:
        raise ContractError("evidence reference fields are invalid")
    if not isinstance(reference["source_id"], str) or not reference["source_id"]:
        raise ContractError("evidence source id is required")
    if not isinstance(reference["source_version"], int) or reference["source_version"] < 1:
        raise ContractError("evidence source version is invalid")
    if not str(reference["source_checksum"]).startswith("sha256:") or len(reference["source_checksum"]) != 71:
        raise ContractError("evidence source checksum is invalid")
    if not isinstance(reference["locator"], str) or not reference["locator"]:
        raise ContractError("evidence locator is required")


def _active_window(record: Mapping[str, Any], moment: datetime, start_field: str, end_field: str) -> bool:
    start = record.get(start_field)
    end = record.get(end_field)
    if start is not None and moment < parse_time(start):
        return False
    if end is not None and moment >= parse_time(end):
        return False
    return True


def _reference_is_active(reference: Mapping[str, Any], sources: Mapping[str, Mapping[str, Any]]) -> bool:
    source = sources.get(str(reference.get("source_id")))
    return source is not None and source["version"] == reference.get("source_version") and source["content_checksum"] == reference.get("source_checksum") and source["locator"] == reference.get("locator")


def _safe_source(record: Mapping[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(record[key]) for key in ("source_id", "source_class", "authority", "locator", "version", "owner_id", "observed_at", "expires_at", "content_checksum")}


def _safe_entity(record: Mapping[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(record[key]) for key in ("entity_id", "entity_type", "canonical_name", "aliases", "version", "content_checksum")}


def _safe_claim(record: Mapping[str, Any], evidence: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "claim_id": record["claim_id"], "subject_entity_id": record["subject_entity_id"],
        "predicate": record["predicate"], "object": copy.deepcopy(record["object"]),
        "owner_id": record["owner_id"], "version": record["version"],
        "valid_from": record["valid_from"], "valid_until": record["valid_until"],
        "evidence": [
            {"evidence_id": item["evidence_id"], "source_ref": copy.deepcopy(item["source_ref"]),
             "extract": item["extract"], "confidence": item["confidence"],
             "content_checksum": item["content_checksum"]}
            for item in evidence
        ],
        "content_checksum": record["content_checksum"],
    }


def _safe_policy(record: Mapping[str, Any]) -> dict[str, Any]:
    return {key: copy.deepcopy(record[key]) for key in ("policy_id", "policy_type", "rule", "owner_id", "approval_ref", "version", "content_checksum")}
