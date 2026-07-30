#!/usr/bin/env python3
"""Bind a read-back Paperclip approval snapshot to the Fleet DMA portal."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agency_os.fleet_portal import FleetPortalAuthority


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--approval-id", required=True)
    parser.add_argument("--approval-checksum", required=True)
    parser.add_argument("--candidate-id")
    args = parser.parse_args()
    record = FleetPortalAuthority(args.database).bind_paperclip_approval(
        actor_id="fleet_platform_administrator", tenant_id="tenant_fleet",
        brand_id="brand_fleet", approval_id=args.approval_id,
        approval_checksum=args.approval_checksum,
        candidate_id=args.candidate_id,
    )
    print(json.dumps(record, sort_keys=True))


if __name__ == "__main__":
    main()
