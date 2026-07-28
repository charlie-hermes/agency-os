"""Checksum-bound Core role bundles and a fresh-process reference loader."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any

from .core_workflow import CORE_RUNTIME_ROLES


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOG = ROOT / "config/runtime-bundles.json"


class RuntimeBundleError(RuntimeError):
    """A role bundle is missing, changed, or outside the repository."""


def _profile(path_text: str, expected: str) -> dict[str, Any]:
    path = (ROOT / path_text).resolve()
    try:
        path.relative_to(ROOT)
    except ValueError as exc:
        raise RuntimeBundleError("runtime profile escaped the repository") from exc
    raw = path.read_bytes()
    observed = hashlib.sha256(raw).hexdigest()
    if observed != expected:
        raise RuntimeBundleError(f"runtime profile checksum changed: {path_text}")
    text = raw.decode("utf-8")
    if not text.strip():
        raise RuntimeBundleError(f"runtime profile is empty: {path_text}")
    return {"path": path_text, "sha256": observed, "bytes": len(raw)}


def verify_bundle_catalog(path: Path = DEFAULT_CATALOG) -> dict[str, Any]:
    catalog = json.loads(path.read_text())
    entries = catalog.get("roles")
    if not isinstance(entries, list):
        raise RuntimeBundleError("runtime bundle catalogue has no roles")
    observed_roles = tuple(item.get("role_id") for item in entries)
    if observed_roles != CORE_RUNTIME_ROLES:
        raise RuntimeBundleError("runtime bundle roles or order changed")
    loaded = []
    for entry in entries:
        loaded.append(
            {
                "role_id": entry["role_id"],
                "agents": _profile(entry["agents_path"], entry["agents_sha256"]),
                "soul": _profile(entry["soul_path"], entry["soul_sha256"]),
                "reference_loader_status": "loaded_in_fresh_process",
                "target_runtime_status": "pending_hermes_runtime",
            }
        )
    return {
        "schema_version": "1.0",
        "bundle_count": len(loaded),
        "target_runtime": catalog.get("target_runtime"),
        "target_runtime_evidence": catalog.get("target_runtime_evidence"),
        "roles": loaded,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=DEFAULT_CATALOG)
    parser.add_argument("--role")
    args = parser.parse_args()
    result = verify_bundle_catalog(args.catalog)
    if args.role:
        result["roles"] = [item for item in result["roles"] if item["role_id"] == args.role]
        if not result["roles"]:
            raise SystemExit("unknown Core runtime role")
        result["bundle_count"] = 1
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
