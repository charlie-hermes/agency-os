#!/usr/bin/env python3
"""Run Fleet's approved, permitted public-search Observatory snapshot."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agency_os.brand_intelligence import BrandIntelligenceAuthority
from agency_os.fleet_brand_runtime import load_fleet_brand_config
from agency_os.fleet_observatory import run_fleet_observatory
from agency_os.store import Principal


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "config/fleet-brand-intelligence.json")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--evidence", type=Path, default=ROOT / "evidence/fleet-public-search-baseline.json")
    args = parser.parse_args()
    config = load_fleet_brand_config(args.config)
    actors = config["actors"]
    result = run_fleet_observatory(
        BrandIntelligenceAuthority(args.database or Path(config["database"])),
        evidence_path=args.evidence, repository_root=ROOT,
        director=Principal(actors["director"], "agency-director", config["brand_id"]),
        analyst=Principal(actors["analyst"], "growth-intelligence-analyst", config["brand_id"]),
        reviewer=Principal("fleet-platform-assurance-reviewer", "platform-assurance-reviewer", config["brand_id"]),
        paperclip_issue_id=config["paperclip_issues"]["closed_loop"],
    )
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
