"""No-network Launch Room scanner and text extractor.

The web tier writes opaque files to an incoming spool. This worker is intended
to run with network namespaces disabled. It scans content before invoking a
bounded parser and emits review candidates; it never admits its own output.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path
from typing import Any, Callable

from .contracts import canonical_bytes, utc_now
from .fleet_portal import SourceAdmissionPolicy, payload_checksum


class FleetIngestError(RuntimeError):
    """A quarantined source could not be safely scanned or extracted."""


def scan_with_clamav(path: Path) -> None:
    result = subprocess.run(
        ["/usr/bin/clamscan", "--no-summary", "--infected", str(path)],
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, timeout=60, check=False,
    )
    if result.returncode == 1:
        raise FleetIngestError("source was rejected by malware scanning")
    if result.returncode != 0:
        raise FleetIngestError("malware scanner failed closed")


def extract_text(path: Path, detected_type: str) -> str:
    if detected_type in {"txt", "csv"}:
        return path.read_text(encoding="utf-8")
    if detected_type == "docx":
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read("word/document.xml"))
        return " ".join(value.strip() for value in root.itertext() if value.strip())
    if detected_type == "xlsx":
        values: list[str] = []
        with zipfile.ZipFile(path) as archive:
            for name in sorted(archive.namelist()):
                if name == "xl/sharedStrings.xml" or name.startswith("xl/worksheets/sheet"):
                    root = ET.fromstring(archive.read(name))
                    values.extend(value.strip() for value in root.itertext() if value.strip())
        return "\n".join(values)
    if detected_type == "pdf":
        command = ["/usr/bin/pdftotext", "-nopgbrk", "-layout", str(path), "-"]
    elif detected_type in {"png", "jpg"}:
        command = ["/usr/bin/tesseract", str(path), "stdout", "--dpi", "300"]
    else:
        raise FleetIngestError("no extractor is admitted for this source type")
    result = subprocess.run(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, timeout=90, check=False,
    )
    if result.returncode != 0:
        raise FleetIngestError("bounded source extraction failed")
    try:
        return result.stdout.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FleetIngestError("extractor output is not valid UTF-8") from exc


def process_source(
    source_path: Path,
    metadata_path: Path,
    output_directory: Path,
    *,
    scanner: Callable[[Path], None] = scan_with_clamav,
    current_case_bytes: int = 0,
) -> dict[str, Any]:
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    required = {
        "source_id", "original_filename", "declared_content_type", "purpose",
        "consent_basis", "tenant_id", "brand_id", "submitted_by", "correlation_id",
    }
    if not isinstance(metadata, dict) or not required.issubset(metadata):
        raise FleetIngestError("source metadata is incomplete")
    source_id = metadata.get("source_id")
    if not isinstance(source_id, str) or not re.fullmatch(r"source_[A-Za-z0-9_-]{1,96}", source_id):
        raise FleetIngestError("source identity is invalid")
    content = source_path.read_bytes()
    scanner(source_path)
    inspection = SourceAdmissionPolicy.inspect_upload(
        filename=metadata["original_filename"],
        declared_content_type=metadata["declared_content_type"],
        content=content, current_case_bytes=current_case_bytes,
        malware_clean=True,
    )
    extracted = extract_text(source_path, inspection["detected_type"])
    if not extracted.strip():
        raise FleetIngestError("source produced no reviewable text")
    if len(extracted.encode("utf-8")) > 10 * 1024 * 1024:
        raise FleetIngestError("extracted text exceeds the admitted limit")
    record = {
        "schema_version": "1.0", "artifact_type": "launch_source_extraction",
        "source_id": metadata["source_id"], "tenant_id": metadata["tenant_id"],
        "brand_id": metadata["brand_id"], "purpose": metadata["purpose"],
        "consent_basis": metadata["consent_basis"], "inspection": inspection,
        "extracted_text": extracted, "state": "review_required",
        "submitted_by": metadata["submitted_by"],
        "correlation_id": metadata["correlation_id"], "extracted_at": utc_now(),
    }
    record["record_checksum"] = payload_checksum(record)
    output_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    output_path = output_directory / f"{metadata['source_id']}.review.json"
    output_path.write_bytes(canonical_bytes(record))
    output_path.chmod(0o600)
    return record


def _existing_case_bytes(spool_root: Path) -> dict[tuple[str, str], int]:
    totals: dict[tuple[str, str], int] = {}
    for directory_name in ("processed",):
        directory = spool_root / directory_name
        if not directory.exists():
            continue
        for metadata_path in directory.glob("*.json"):
            if metadata_path.name.endswith(".error.json"):
                continue
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                source_path = Path(str(metadata_path)[:-5])
                key = (str(metadata["tenant_id"]), str(metadata["brand_id"]))
                if source_path.is_file() and not source_path.is_symlink():
                    totals[key] = totals.get(key, 0) + source_path.stat().st_size
            except (OSError, KeyError, TypeError, json.JSONDecodeError):
                continue
    return totals


def purge_rejected_uploads(spool_root: Path, *, now: float | None = None) -> int:
    rejected = spool_root / "rejected"
    if not rejected.exists():
        return 0
    cutoff = (time.time() if now is None else now) - 7 * 24 * 60 * 60
    removed = 0
    for path in rejected.iterdir():
        try:
            if path.is_file() and not path.is_symlink() and path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed


def process_spool(
    spool_root: Path, *, scanner: Callable[[Path], None] = scan_with_clamav,
) -> dict[str, int]:
    """Process complete source/metadata pairs without following symlinks."""

    incoming = spool_root / "incoming"
    review = spool_root / "review"
    processed = spool_root / "processed"
    rejected = spool_root / "rejected"
    for directory in (incoming, review, processed, rejected):
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    result = {"review_required": 0, "rejected": 0, "incomplete": 0}
    purge_rejected_uploads(spool_root)
    case_bytes = _existing_case_bytes(spool_root)
    for source in sorted(incoming.iterdir()):
        if source.name.endswith((".json", ".part")):
            continue
        metadata = Path(f"{source}.json")
        if not metadata.exists():
            result["incomplete"] += 1
            continue
        if source.is_symlink() or metadata.is_symlink() or not source.is_file() or not metadata.is_file():
            result["rejected"] += 1
            continue
        try:
            metadata_value = json.loads(metadata.read_text(encoding="utf-8"))
            source_id = metadata_value.get("source_id")
            if not isinstance(source_id, str) or not source.name.startswith(f"{source_id}."):
                raise FleetIngestError("spooled source filename does not match its metadata")
            key = (str(metadata_value.get("tenant_id", "")), str(metadata_value.get("brand_id", "")))
            record = process_source(
                source, metadata, review, scanner=scanner,
                current_case_bytes=case_bytes.get(key, 0),
            )
        except (FleetIngestError, OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile):
            destination = rejected / source.name
            shutil.move(str(source), destination)
            shutil.move(str(metadata), Path(f"{destination}.json"))
            error_path = rejected / f"{source.name}.error.json"
            error_path.write_text(
                json.dumps({"status": "rejected", "reason": "source_admission_failed"}),
                encoding="utf-8",
            )
            os.chmod(error_path, 0o600)
            result["rejected"] += 1
            continue
        destination = processed / source.name
        shutil.move(str(source), destination)
        shutil.move(str(metadata), Path(f"{destination}.json"))
        case_bytes[key] = case_bytes.get(key, 0) + destination.stat().st_size
        result[record["state"]] += 1
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--source", type=Path)
    mode.add_argument("--spool-root", type=Path)
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--metadata", type=Path)
    parser.add_argument("--output-directory", type=Path)
    args = parser.parse_args()
    if args.spool_root is not None:
        while True:
            print(json.dumps(process_spool(args.spool_root), sort_keys=True), flush=True)
            if not args.watch:
                return
            time.sleep(max(args.poll_seconds, 0.25))
    if args.metadata is None or args.output_directory is None:
        parser.error("--metadata and --output-directory are required with --source")
    result = process_source(args.source, args.metadata, args.output_directory)
    print(json.dumps({
        "status": "review_required", "source_id": result["source_id"],
        "record_checksum": result["record_checksum"],
    }, sort_keys=True))


if __name__ == "__main__":
    main()
