"""Fail-closed admission for the installed Paperclip and Buzz contracts.

The checked-in manifest contains only read-only, non-secret target-host
evidence.  It does not authorize authenticated calls or production activation.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Sequence

from .contracts import ContractError, parse_time


class PlatformCompatibilityError(ContractError):
    """Installed platform evidence is missing, unsafe, or has drifted."""


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "config" / "installed-platforms.json"

_HEX = frozenset("0123456789abcdef")
_SECRET_KEY_FRAGMENTS = (
    "api_key",
    "auth_tag",
    "credential",
    "password",
    "private_key",
    "secret",
    "token",
)

PAPERCLIP_REQUIRED_SURFACE = frozenset(
    {
        ("GET", "/api/health"),
        ("GET", "/api/agents/me"),
        ("GET", "/api/companies/:companyId/issues"),
        ("POST", "/api/companies/:companyId/issues"),
        ("GET", "/api/issues/:issueId"),
        ("PATCH", "/api/issues/:issueId"),
        ("POST", "/api/issues/:issueId/checkout"),
        ("POST", "/api/issues/:issueId/release"),
        ("GET", "/api/issues/:issueId/comments"),
        ("POST", "/api/issues/:issueId/comments"),
        ("GET", "/api/issues/:issueId/approvals"),
        ("POST", "/api/issues/:issueId/approvals"),
        ("GET", "/api/companies/:companyId/approvals"),
        ("POST", "/api/companies/:companyId/approvals"),
        ("GET", "/api/approvals/:approvalId"),
        ("GET", "/api/approvals/:approvalId/issues"),
        ("POST", "/api/approvals/:approvalId/approve"),
        ("POST", "/api/approvals/:approvalId/reject"),
        ("POST", "/api/companies/:companyId/cost-events"),
        ("GET", "/api/companies/:companyId/costs/summary"),
        ("PATCH", "/api/companies/:companyId/budgets"),
    }
)

PAPERCLIP_SOURCE_FALLBACK_SURFACE = frozenset(
    {
        ("PATCH", "/api/companies/:companyId/budgets"),
    }
)

BUZZ_REQUIRED_SURFACE = {
    "messages send": frozenset({"--channel", "--content", "--reply-to", "--file"}),
    "messages get": frozenset(
        {"--channel", "--limit", "--before", "--since", "--kinds"}
    ),
    "messages thread": frozenset(
        {"--channel", "--event", "--limit", "--depth-limit"}
    ),
    "messages search": frozenset({"--query", "--author", "--since", "--limit"}),
    "channels create": frozenset(
        {"--name", "--type", "--visibility", "--description", "--ttl"}
    ),
    "channels get": frozenset({"--channel"}),
    "channels list": frozenset({"--visibility", "--member", "--limit"}),
    "channels join": frozenset({"--channel"}),
    "channels members": frozenset({"--channel"}),
}

_PAPERCLIP_SOURCE_PATHS = {
    "health_routes": "node_modules/@paperclipai/server/dist/routes/health.js",
    "agent_routes": "node_modules/@paperclipai/server/dist/routes/agents.js",
    "issue_routes": "node_modules/@paperclipai/server/dist/routes/issues.js",
    "approval_routes": "node_modules/@paperclipai/server/dist/routes/approvals.js",
    "cost_routes": "node_modules/@paperclipai/server/dist/routes/costs.js",
    "api_reference": (
        "node_modules/@paperclipai/server/skills/paperclip/"
        "references/api-reference.md"
    ),
}


def _require_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PlatformCompatibilityError(f"{label} must be an object")
    return value


def _require_string(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise PlatformCompatibilityError(f"{label} must be a non-empty string")
    return value


def _validate_sha256(value: Any, label: str) -> str:
    digest = _require_string(value, label)
    if len(digest) != 64 or any(character not in _HEX for character in digest):
        raise PlatformCompatibilityError(f"{label} must be a lowercase SHA-256")
    return digest


def _deny_secret_fields(value: Any, path: str = "manifest") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            lowered = str(key).lower()
            if any(fragment in lowered for fragment in _SECRET_KEY_FRAGMENTS):
                raise PlatformCompatibilityError(
                    f"{path}.{key} is a forbidden secret-bearing field"
                )
            _deny_secret_fields(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _deny_secret_fields(child, f"{path}[{index}]")


def validate_installed_platform_manifest(
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate non-secret target-host evidence and required adapter surfaces."""

    record = dict(_require_mapping(manifest, "manifest"))
    _deny_secret_fields(record)
    if record.get("schema_version") != "1.0":
        raise PlatformCompatibilityError("unsupported manifest schema version")
    if record.get("capture_mode") != "read_only":
        raise PlatformCompatibilityError("installed platform capture must be read-only")
    if record.get("real_external_writes") is not False:
        raise PlatformCompatibilityError("compatibility evidence must make no write")
    parse_time(_require_string(record.get("captured_at"), "captured_at"))
    _require_string(record.get("host_id"), "host_id")

    paperclip = _require_mapping(record.get("paperclip"), "paperclip")
    version = _require_string(
        paperclip.get("package_version"), "paperclip.package_version"
    )
    package_root = Path(
        _require_string(paperclip.get("package_root"), "paperclip.package_root")
    )
    if not package_root.is_absolute() or version not in package_root.parts:
        raise PlatformCompatibilityError(
            "Paperclip package root must be absolute and version-pinned"
        )
    package_json_path = Path(
        _require_string(
            paperclip.get("package_json_path"), "paperclip.package_json_path"
        )
    )
    if (
        not package_json_path.is_absolute()
        or package_root not in package_json_path.parents
    ):
        raise PlatformCompatibilityError(
            "Paperclip package metadata must live below its versioned root"
        )
    _validate_sha256(
        paperclip.get("package_json_sha256"), "paperclip.package_json_sha256"
    )
    _validate_sha256(
        paperclip.get("package_lock_sha256"), "paperclip.package_lock_sha256"
    )
    source_sha256 = _require_mapping(
        paperclip.get("source_sha256"), "paperclip.source_sha256"
    )
    if set(source_sha256) != set(_PAPERCLIP_SOURCE_PATHS):
        raise PlatformCompatibilityError("Paperclip source evidence set is incomplete")
    for name, digest in source_sha256.items():
        _validate_sha256(digest, f"paperclip.source_sha256.{name}")

    service = _require_mapping(paperclip.get("service"), "paperclip.service")
    expected_service = {
        "unit": "paperclip.service",
        "user": "paperclip",
        "group": "paperclip",
        "active_state": "active",
        "sub_state": "running",
        "protect_system": "strict",
        "protect_home": True,
        "no_new_privileges": True,
        "instance": "default",
    }
    for field, expected in expected_service.items():
        if service.get(field) != expected:
            raise PlatformCompatibilityError(
                f"Paperclip service field {field!r} is not safely admitted"
            )
    for field in (
        "fragment_path",
        "executable",
        "executable_resolved_path",
        "data_root",
        "workspace_root",
    ):
        service_path = Path(
            _require_string(service.get(field), f"paperclip.service.{field}")
        )
        if not service_path.is_absolute():
            raise PlatformCompatibilityError(
                f"Paperclip service field {field!r} must be absolute"
            )
    _validate_sha256(
        service.get("fragment_sha256"), "paperclip.service.fragment_sha256"
    )
    _validate_sha256(
        service.get("executable_sha256"),
        "paperclip.service.executable_sha256",
    )

    executable_path = Path(service["executable"])
    resolved_executable_path = Path(service["executable_resolved_path"])
    if (
        package_root not in executable_path.parents
        or package_root not in resolved_executable_path.parents
    ):
        raise PlatformCompatibilityError(
            "Paperclip service executable paths are outside the pinned package root"
        )

    health = _require_mapping(paperclip.get("health"), "paperclip.health")
    expected_health = {
        "status": "ok",
        "deployment_mode": "authenticated",
        "deployment_exposure": "private",
        "bootstrap_status": "ready",
        "bootstrap_invite_active": False,
    }
    for field, expected in expected_health.items():
        if health.get(field) != expected:
            raise PlatformCompatibilityError(
                f"Paperclip health field {field!r} is not safely admitted"
            )
    health_url = _require_string(health.get("url"), "paperclip.health.url")
    parsed_health_url = urllib.parse.urlparse(health_url)
    try:
        health_address = ipaddress.ip_address(parsed_health_url.hostname or "")
    except ValueError as exc:
        raise PlatformCompatibilityError(
            "Paperclip health host must be an IP address"
        ) from exc
    if (
        parsed_health_url.scheme != "http"
        or parsed_health_url.path != "/api/health"
        or parsed_health_url.params
        or parsed_health_url.query
        or parsed_health_url.fragment
        or parsed_health_url.username is not None
        or parsed_health_url.password is not None
        or not health_address.is_private
    ):
        raise PlatformCompatibilityError(
            "Paperclip health URL is not a private HTTP probe"
        )

    raw_surface = paperclip.get("api_surface")
    if not isinstance(raw_surface, list):
        raise PlatformCompatibilityError("paperclip.api_surface must be an array")
    surface: set[tuple[str, str]] = set()
    for item in raw_surface:
        if (
            not isinstance(item, list)
            or len(item) != 2
            or not all(isinstance(part, str) and part for part in item)
        ):
            raise PlatformCompatibilityError("invalid Paperclip API surface entry")
        surface.add((item[0], item[1]))
    if len(surface) != len(raw_surface):
        raise PlatformCompatibilityError("Paperclip API surface contains duplicates")
    missing_paperclip = PAPERCLIP_REQUIRED_SURFACE - surface
    extra_paperclip = surface - PAPERCLIP_REQUIRED_SURFACE
    if missing_paperclip or extra_paperclip:
        raise PlatformCompatibilityError(
            "Paperclip API surface must match the reviewed contract exactly: "
            f"missing={sorted(missing_paperclip)!r}, "
            f"extra={sorted(extra_paperclip)!r}"
        )

    buzz = _require_mapping(record.get("buzz"), "buzz")
    if buzz.get("version_strategy") != "binary_sha256_and_cli_surface":
        raise PlatformCompatibilityError("Buzz version identity strategy is unsupported")
    if buzz.get("version_flag_supported") is not False:
        raise PlatformCompatibilityError(
            "Buzz manifest must record that no version flag is available"
        )
    buzz_path = Path(
        _require_string(buzz.get("binary_path"), "buzz.binary_path")
    )
    if not buzz_path.is_absolute():
        raise PlatformCompatibilityError("Buzz binary path must be absolute")
    _validate_sha256(buzz.get("binary_sha256"), "buzz.binary_sha256")
    if (
        not isinstance(buzz.get("binary_size_bytes"), int)
        or buzz["binary_size_bytes"] < 1
    ):
        raise PlatformCompatibilityError("Buzz binary size must be positive")
    command_surface = _require_mapping(
        buzz.get("command_surface"), "buzz.command_surface"
    )
    for command, required_options in BUZZ_REQUIRED_SURFACE.items():
        options = command_surface.get(command)
        if not isinstance(options, list) or not all(
            isinstance(option, str) and option.startswith("--") for option in options
        ):
            raise PlatformCompatibilityError(
                f"Buzz command {command!r} has an invalid option surface"
            )
        if not required_options.issubset(options):
            raise PlatformCompatibilityError(
                f"Buzz command {command!r} is missing required options"
            )
    policy = _require_mapping(buzz.get("adapter_policy"), "buzz.adapter_policy")
    if "--broadcast" not in policy.get("denied_options", []):
        raise PlatformCompatibilityError("Buzz broadcast must remain denied")
    if policy.get("task_state_mutation") is not False:
        raise PlatformCompatibilityError("Buzz may not mutate task state")
    if policy.get("decision_write_back_authority") != "paperclip":
        raise PlatformCompatibilityError(
            "Buzz decisions must write back through Paperclip"
        )
    return json.loads(json.dumps(record))


def load_installed_platform_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    return validate_installed_platform_manifest(json.loads(path.read_text()))


def admit_installed_platform_manifest(
    observed: Mapping[str, Any],
    *,
    baseline: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Require an observed contract to match the reviewed baseline exactly.

    A later read-only recapture may have a different timestamp. Every identity,
    checksum, route, command, service, health, and authority field remains
    pinned until a reviewed manifest update explicitly changes it.
    """

    observed_record = validate_installed_platform_manifest(observed)
    baseline_record = validate_installed_platform_manifest(
        baseline
        if baseline is not None
        else json.loads(DEFAULT_MANIFEST.read_text())
    )
    observed_record.pop("captured_at")
    baseline_record.pop("captured_at")
    if observed_record != baseline_record:
        raise PlatformCompatibilityError(
            "installed platform contract differs from the reviewed baseline"
        )
    return validate_installed_platform_manifest(observed)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _run_text(command: Sequence[str]) -> str:
    try:
        completed = subprocess.run(
            list(command),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise PlatformCompatibilityError(
            f"read-only platform command failed: {command[0]}"
        ) from exc
    return completed.stdout


def _verify_file(path: Path, expected_digest: str, label: str) -> None:
    if not path.is_file():
        raise PlatformCompatibilityError(f"{label} is missing at {path}")
    observed_digest = _sha256_file(path)
    if observed_digest != expected_digest:
        raise PlatformCompatibilityError(
            f"{label} checksum drift: expected {expected_digest}, got {observed_digest}"
        )


def _verify_paperclip_surface(
    reference_path: Path,
    cost_routes_path: Path,
    surface: Sequence[Sequence[str]],
) -> None:
    reference = reference_path.read_text()
    cost_routes = cost_routes_path.read_text()
    for method, route in surface:
        if route == "/api/health":
            continue
        pattern = rf"\|\s*{re.escape(method)}\s*\|\s*`{re.escape(route)}`"
        if re.search(pattern, reference) is not None:
            continue
        source_declaration = (
            f'router.{method.lower()}("{route.removeprefix("/api")}"'
        )
        if (
            (method, route) in PAPERCLIP_SOURCE_FALLBACK_SURFACE
            and source_declaration in cost_routes
        ):
            continue
        raise PlatformCompatibilityError(
            f"Paperclip installed sources do not declare {method} {route}"
        )


def verify_live_installed_platforms(
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Re-run the read-only target-host checks represented by the manifest."""

    record = admit_installed_platform_manifest(manifest)
    paperclip = record["paperclip"]
    service = paperclip["service"]
    executable_path = Path(service["executable"])
    _verify_file(
        executable_path,
        service["executable_sha256"],
        "Paperclip executable",
    )
    if executable_path.resolve() != Path(service["executable_resolved_path"]):
        raise PlatformCompatibilityError("Paperclip executable target drift")
    _verify_file(
        Path(service["fragment_path"]),
        service["fragment_sha256"],
        "Paperclip service unit",
    )

    package_root = Path(paperclip["package_root"])
    package_json_path = Path(paperclip["package_json_path"])
    _verify_file(
        package_json_path,
        paperclip["package_json_sha256"],
        "Paperclip package metadata",
    )
    package = json.loads(package_json_path.read_text())
    if package.get("name") != paperclip["package_name"]:
        raise PlatformCompatibilityError("Paperclip package name drift")
    if package.get("version") != paperclip["package_version"]:
        raise PlatformCompatibilityError("Paperclip package version drift")
    _verify_file(
        package_root / "package-lock.json",
        paperclip["package_lock_sha256"],
        "Paperclip lockfile",
    )
    for name, relative_path in _PAPERCLIP_SOURCE_PATHS.items():
        _verify_file(
            package_root / relative_path,
            paperclip["source_sha256"][name],
            f"Paperclip {name}",
        )

    _verify_paperclip_surface(
        package_root / _PAPERCLIP_SOURCE_PATHS["api_reference"],
        package_root / _PAPERCLIP_SOURCE_PATHS["cost_routes"],
        paperclip["api_surface"],
    )
    service_output = _run_text(
        (
            "systemctl",
            "show",
            paperclip["service"]["unit"],
            "-p",
            "ActiveState",
            "-p",
            "SubState",
            "-p",
            "User",
            "-p",
            "Group",
            "-p",
            "FragmentPath",
            "-p",
            "ProtectSystem",
            "-p",
            "ProtectHome",
            "-p",
            "NoNewPrivileges",
            "-p",
            "WorkingDirectory",
            "-p",
            "ReadWritePaths",
            "-p",
            "ExecStart",
            "--no-pager",
        )
    )
    service_facts = {}
    for line in service_output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            service_facts[key] = value
    exec_start = service_facts.pop("ExecStart", "")
    expected_read_write_paths = " ".join(
        (
            paperclip["service"]["data_root"],
            paperclip["service"]["workspace_root"],
        )
    )
    expected_service_facts = {
        "ActiveState": "active",
        "SubState": "running",
        "User": paperclip["service"]["user"],
        "Group": paperclip["service"]["group"],
        "FragmentPath": paperclip["service"]["fragment_path"],
        "ProtectSystem": "strict",
        "ProtectHome": "yes",
        "NoNewPrivileges": "yes",
        "WorkingDirectory": paperclip["service"]["data_root"],
        "ReadWritePaths": expected_read_write_paths,
    }
    if service_facts != expected_service_facts:
        raise PlatformCompatibilityError(
            "Paperclip service identity or hardening drift"
        )
    expected_argv = (
        f'{paperclip["service"]["executable"]} run '
        f'-d {paperclip["service"]["data_root"]} '
        f'-i {paperclip["service"]["instance"]} --no-repair'
    )
    exec_match = re.match(
        r"^\{ path=(\S+) ; argv\[\]=(.*?) ; ignore_errors=", exec_start
    )
    if exec_match is None or exec_match.groups() != (
        paperclip["service"]["executable"],
        expected_argv,
    ):
        raise PlatformCompatibilityError("Paperclip service command drift")

    try:
        with urllib.request.urlopen(
            paperclip["health"]["url"], timeout=5
        ) as response:
            health = json.load(response)
    except (OSError, json.JSONDecodeError) as exc:
        raise PlatformCompatibilityError("Paperclip health probe failed") from exc
    observed_health = {
        "status": health.get("status"),
        "deployment_mode": health.get("deploymentMode"),
        "deployment_exposure": health.get("deploymentExposure"),
        "bootstrap_status": health.get("bootstrapStatus"),
        "bootstrap_invite_active": health.get("bootstrapInviteActive"),
    }
    expected_health = {
        field: value for field, value in paperclip["health"].items() if field != "url"
    }
    if observed_health != expected_health:
        raise PlatformCompatibilityError("Paperclip live health contract drift")

    buzz = record["buzz"]
    buzz_path = Path(buzz["binary_path"])
    _verify_file(buzz_path, buzz["binary_sha256"], "Buzz binary")
    if buzz_path.stat().st_size != buzz["binary_size_bytes"]:
        raise PlatformCompatibilityError("Buzz binary size drift")
    for command, required_options in buzz["command_surface"].items():
        output = _run_text((str(buzz_path), *command.split(), "--help"))
        missing = [option for option in required_options if option not in output]
        if missing:
            raise PlatformCompatibilityError(
                f"Buzz command {command!r} help is missing {missing!r}"
            )

    return {
        "status": "compatible",
        "capture_mode": "read_only",
        "host_id": record["host_id"],
        "paperclip_version": paperclip["package_version"],
        "buzz_identity": f"sha256:{buzz['binary_sha256']}",
        "real_external_writes": False,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the checked-in installed Paperclip/Buzz contract read-only."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="installed platform manifest to verify",
    )
    args = parser.parse_args(argv)
    report = verify_live_installed_platforms(
        load_installed_platform_manifest(args.manifest)
    )
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
