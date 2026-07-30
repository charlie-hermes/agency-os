from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from agency_os.fleet_portal import FleetPortalAuthority


class FleetPortalInitializerTests(unittest.TestCase):
    def test_initializer_is_idempotent_and_admits_only_fleet_dma(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "portal.sqlite3"
            command = [
                "python3", "scripts/initialize_fleet_portal.py", "--database", str(database),
                "--workos-organization-id", "org_fleet_test",
                "--workos-subject", "user_fleet_owner_test",
            ]
            first = subprocess.run(command, check=True, capture_output=True, text=True)
            second = subprocess.run(command, check=True, capture_output=True, text=True)
            self.assertEqual(first.stdout, second.stdout)
            authority = FleetPortalAuthority(database)
            context = authority.resolve_verified_identity(
                workos_subject="user_fleet_owner_test", workos_organization_id="org_fleet_test",
                hostname="fleet.madebyfleet.com", origin="https://fleet.madebyfleet.com",
                access_identity_verified=True, session_id="workos:test",
                correlation_id="initializer_test",
            )
            self.assertEqual(context.tenant_id, "tenant_fleet")
            self.assertEqual(context.brand_id, "brand_fleet")
            self.assertEqual(len(authority.list_content(context)), 1)


if __name__ == "__main__":
    unittest.main()
