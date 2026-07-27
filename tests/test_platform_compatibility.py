from __future__ import annotations

import copy
import json
import unittest

from agency_os.platform_compatibility import (
    DEFAULT_MANIFEST,
    PlatformCompatibilityError,
    admit_installed_platform_manifest,
    load_installed_platform_manifest,
    validate_installed_platform_manifest,
)


class PlatformCompatibilityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = json.loads(DEFAULT_MANIFEST.read_text())

    def test_checked_in_target_host_manifest_is_admitted(self) -> None:
        admitted = load_installed_platform_manifest()

        self.assertEqual(admitted["host_id"], "paperclip-511e4513")
        self.assertEqual(admitted["paperclip"]["package_version"], "2026.720.0")
        self.assertEqual(
            admitted["buzz"]["version_strategy"],
            "binary_sha256_and_cli_surface",
        )
        self.assertFalse(admitted["real_external_writes"])
        self.assertEqual(
            admitted["paperclip"]["health"]["deployment_exposure"], "private"
        )

    def test_paperclip_version_identity_service_and_route_drift_fail_closed(self) -> None:
        mutations = (
            (("paperclip", "package_version"), "2026.721.0"),
            (("paperclip", "service", "user"), "ubuntu"),
            (("paperclip", "service", "protect_system"), "full"),
            (("paperclip", "service", "instance"), "attacker"),
            (("paperclip", "health", "deployment_exposure"), "public"),
        )
        for path, replacement in mutations:
            with self.subTest(path=path):
                changed = copy.deepcopy(self.manifest)
                target = changed
                for component in path[:-1]:
                    target = target[component]
                target[path[-1]] = replacement
                with self.assertRaises(PlatformCompatibilityError):
                    validate_installed_platform_manifest(changed)

        changed = copy.deepcopy(self.manifest)
        changed["paperclip"]["api_surface"].remove(
            ["POST", "/api/approvals/:approvalId/approve"]
        )
        with self.assertRaises(PlatformCompatibilityError):
            validate_installed_platform_manifest(changed)

        changed = copy.deepcopy(self.manifest)
        changed["paperclip"]["package_json_sha256"] = "0" * 64
        with self.assertRaises(PlatformCompatibilityError):
            admit_installed_platform_manifest(changed)

        changed = copy.deepcopy(self.manifest)
        changed["paperclip"]["health"]["url"] = "http://8.8.8.8/api/health"
        with self.assertRaises(PlatformCompatibilityError):
            validate_installed_platform_manifest(changed)

    def test_buzz_binary_command_or_authority_drift_fails_closed(self) -> None:
        changed = copy.deepcopy(self.manifest)
        changed["buzz"]["binary_sha256"] = "f" * 64
        with self.assertRaises(PlatformCompatibilityError):
            admit_installed_platform_manifest(changed)

        changed = copy.deepcopy(self.manifest)
        changed["buzz"]["command_surface"]["messages send"].remove("--reply-to")
        with self.assertRaises(PlatformCompatibilityError):
            validate_installed_platform_manifest(changed)

        changed = copy.deepcopy(self.manifest)
        changed["buzz"]["adapter_policy"]["denied_options"] = []
        with self.assertRaises(PlatformCompatibilityError):
            validate_installed_platform_manifest(changed)

        changed = copy.deepcopy(self.manifest)
        changed["buzz"]["adapter_policy"]["task_state_mutation"] = True
        with self.assertRaises(PlatformCompatibilityError):
            validate_installed_platform_manifest(changed)

    def test_secret_bearing_capture_fields_are_rejected(self) -> None:
        forbidden_fields = (
            "api_key",
            "auth_tag",
            "credential_path",
            "password",
            "private_key",
            "secret_value",
            "token",
        )
        for field in forbidden_fields:
            with self.subTest(field=field):
                changed = copy.deepcopy(self.manifest)
                changed["paperclip"][field] = "must-not-be-captured"
                with self.assertRaises(PlatformCompatibilityError):
                    validate_installed_platform_manifest(changed)

    def test_manifest_paths_are_absolute_and_version_pinned(self) -> None:
        changed = copy.deepcopy(self.manifest)
        changed["paperclip"]["package_root"] = "/opt/paperclip/current"
        with self.assertRaises(PlatformCompatibilityError):
            validate_installed_platform_manifest(changed)

        changed = copy.deepcopy(self.manifest)
        changed["buzz"]["binary_path"] = "buzz"
        with self.assertRaises(PlatformCompatibilityError):
            validate_installed_platform_manifest(changed)


if __name__ == "__main__":
    unittest.main()
