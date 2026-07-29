#!/usr/bin/env python3
"""Read-only verification of Fleet's live G2.2-G2.4 intelligence authority."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agency_os.brand_intelligence import BrandIntelligenceError, _validate_record
from agency_os.sqlite_storage import SQLiteStorageError, validate_sqlite_storage

EXPECTED_COUNTS = {
    "brand_source": 4, "brand_entity": 8, "brand_claim": 8,
    "claim_evidence": 8, "brand_policy": 6, "customer_mission": 50,
    "observation_run": 2, "observation": 40, "market_finding": 1,
    "remediation_proposal": 1, "experiment": 1, "outcome_event": 1,
}


def verify_live_intelligence(database_path: Path) -> dict[str, Any]:
    try:
        validate_sqlite_storage(database_path)
    except SQLiteStorageError as exc:
        raise BrandIntelligenceError("unsafe live Brand Intelligence storage") from exc
    connection = sqlite3.connect(f"file:{database_path}?mode=ro", uri=True, isolation_level=None)
    try:
        connection.execute("PRAGMA query_only=ON")
        version = connection.execute("SELECT version FROM schema_metadata WHERE id=1").fetchone()
        integrity = connection.execute("PRAGMA quick_check").fetchone()
        if version != (1,) or integrity != ("ok",):
            raise BrandIntelligenceError("live Brand Intelligence schema or integrity failed")
        rows = connection.execute(
            "SELECT artifact_type,record_id,version,record_json FROM records WHERE brand_id='brand_fleet'"
        ).fetchall()
        counts: dict[str, int] = {}
        latest: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
        for artifact_type, record_id, version_number, encoded in rows:
            record = json.loads(encoded)
            _validate_record(record)
            if record["brand_id"] != "brand_fleet":
                raise BrandIntelligenceError("stored Fleet record crossed its tenant boundary")
            key = (artifact_type, record_id)
            if key not in latest or version_number > latest[key][0]:
                latest[key] = (version_number, record)
        for (artifact_type, _record_id), (_version, record) in latest.items():
            if record["status"] in {"active", "complete"}:
                counts[artifact_type] = counts.get(artifact_type, 0) + 1
        if counts != EXPECTED_COUNTS:
            raise BrandIntelligenceError(f"live Fleet record counts differ: {counts}")
        launch = [
            record for (artifact_type, _), (_, record) in latest.items()
            if artifact_type == "customer_mission"
            and record["success_definition"].get("launch_priority") is True
        ]
        if len(launch) != 20:
            raise BrandIntelligenceError("live Fleet launch mission count differs")
        approvals = connection.execute(
            "SELECT artifact_type,COUNT(*),COUNT(DISTINCT paperclip_approval_id) FROM record_approvals WHERE brand_id='brand_fleet' GROUP BY artifact_type"
        ).fetchall()
        approval_counts = {row[0]: {"record_count": row[1], "approval_count": row[2]} for row in approvals}
        if approval_counts != {
            "brand_claim": {"record_count": 8, "approval_count": 1},
            "remediation_proposal": {"record_count": 1, "approval_count": 1},
        }:
            raise BrandIntelligenceError("live Fleet Paperclip approval bindings differ")
        outcome = latest[("outcome_event", "outcome_fleet_controlled_preview_v1")][1]
        if outcome["causal_status"] != "observed" or outcome["value"] != 1:
            raise BrandIntelligenceError("controlled preview outcome is overstated or incomplete")
        audit_count = connection.execute(
            "SELECT COUNT(*) FROM authority_audit WHERE brand_id='brand_fleet'"
        ).fetchone()[0]
    finally:
        connection.close()
    return {
        "status": "pass", "brand_id": "brand_fleet", "schema_version": 1,
        "record_counts": counts, "launch_mission_count": len(launch),
        "approval_bindings": approval_counts, "audit_event_count": audit_count,
        "external_ai_coverage": "unknown", "provider_external_writes": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify_live_intelligence(args.database), sort_keys=True))


if __name__ == "__main__":
    main()
