#!/usr/bin/env python3
"""Initialise Fleet's live Brand Twin and customer-mission authority."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agency_os.fleet_brand_runtime import (
    build_fleet_records,
    claim_approval_package,
    initialise_fleet_brand_intelligence,
    load_fleet_brand_config,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=ROOT / "config/fleet-brand-intelligence.json")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--paperclip-approval", type=Path)
    parser.add_argument("--approved-by", default="human_owner")
    parser.add_argument("--print-approval-package", action="store_true")
    parser.add_argument("--assert-company-id-file", type=Path)
    args = parser.parse_args()

    config = load_fleet_brand_config(args.config)
    records = build_fleet_records(config, ROOT)
    package = claim_approval_package(records)
    if args.print_approval_package:
        print(json.dumps(package, sort_keys=True, indent=2))
        return
    if args.paperclip_approval is None:
        raise SystemExit("initialization requires --paperclip-approval")
    database = args.database or Path(config["database"])
    if database.resolve() == Path(config["database"]).resolve():
        if args.assert_company_id_file is None:
            raise SystemExit("production initialization requires --assert-company-id-file")
        company_id = args.assert_company_id_file.read_text(encoding="utf-8").strip()
        if company_id != "d7e2e389-c7ad-486e-87ca-482e4ec6216d":
            raise SystemExit("live Paperclip company ID is not Fleet DMA")
    approval = json.loads(args.paperclip_approval.read_text(encoding="utf-8"))
    result = initialise_fleet_brand_intelligence(
        config, ROOT, database, approval, approved_by=args.approved_by,
    )
    print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == "__main__":
    main()
