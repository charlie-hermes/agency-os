from __future__ import annotations

import copy
import io
import json
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from agency_os.contracts import ContractError
from agency_os.fleet_tenancy import FleetTenancyError, FleetTenantAuthority
from agency_os.store import Principal
from scripts.initialize_fleet_tenant import initialise, load_config, main
from scripts.verify_fleet_tenant import verify_foundation


ROOT = Path(__file__).resolve().parents[1]
FLEET_COMPANY_ID = "d7e2e389-c7ad-486e-87ca-482e4ec6216d"


class FleetInitializerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = load_config(ROOT / "config/fleet-generation2.json")

    def test_initializer_is_atomic_and_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "fleet.sqlite3"
            first = initialise(self.config, database_path)
            second = initialise(self.config, database_path)
            self.assertEqual(first, second)
            self.assertEqual(first["schema_version"], 2)
            self.assertEqual(first["enabled_modules"], ["ai_market_observatory", "brand_twin", "content_engine"])

    def test_late_entitlement_failure_leaves_no_partial_tenant_or_hostname(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database_path = Path(temporary_directory) / "fleet.sqlite3"
            invalid = copy.deepcopy(self.config)
            entitlement = invalid["product_entitlements"][0]
            entitlement["version"] = 2
            entitlement["supersedes_entitlement_id"] = "missing_v1"
            with self.assertRaises(ContractError):
                initialise(invalid, database_path)

            authority = FleetTenantAuthority(database_path)
            reviewer = Principal(
                "reviewer_fleet", "platform-assurance-reviewer", "brand_fleet"
            )
            with self.assertRaises(KeyError):
                authority.get_tenant(reviewer)
            with self.assertRaises(KeyError):
                authority.authorize_hostname(reviewer, "fleet.madebyfleet.com")

    def test_live_verifier_is_read_only_and_fails_closed_on_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database_path = root / "fleet.sqlite3"
            config_path = root / "config.json"
            config_path.write_text(json.dumps(self.config))
            company_pin = root / "company-id"
            company_pin.write_text(FLEET_COMPANY_ID + "\n")
            initialise(self.config, database_path)

            connection = sqlite3.connect(database_path)
            before = connection.execute("SELECT COUNT(*) FROM authority_audit").fetchone()[0]
            connection.close()
            result = verify_foundation(config_path, database_path, company_pin)
            self.assertEqual(result["enabled_modules"], ["ai_market_observatory", "brand_twin", "content_engine"])
            connection = sqlite3.connect(database_path)
            after = connection.execute("SELECT COUNT(*) FROM authority_audit").fetchone()[0]
            connection.execute(
                "UPDATE portal_hostnames SET state = 'suspended' WHERE hostname = ?",
                ("fleet.madebyfleet.com",),
            )
            connection.commit()
            connection.close()
            self.assertEqual(before, after)
            with self.assertRaises(FleetTenancyError):
                verify_foundation(config_path, database_path, company_pin)

    def test_production_path_requires_and_checks_company_pin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database_path = root / "fleet.sqlite3"
            config = copy.deepcopy(self.config)
            config["authority_database"] = str(database_path)
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config))

            with patch("sys.argv", ["initialize", "--config", str(config_path)]):
                with self.assertRaisesRegex(SystemExit, "requires --assert-company-id-file"):
                    main()

            wrong_pin = root / "wrong-company-id"
            wrong_pin.write_text("63d47cf2-df2d-4fbb-88e7-d8db70bddcec\n")
            with patch(
                "sys.argv",
                ["initialize", "--config", str(config_path), "--assert-company-id-file", str(wrong_pin)],
            ):
                with self.assertRaisesRegex(SystemExit, "does not match"):
                    main()

            company_pin = root / "company-id"
            company_pin.write_text(FLEET_COMPANY_ID + "\n")
            with patch(
                "sys.argv",
                ["initialize", "--config", str(config_path), "--assert-company-id-file", str(company_pin)],
            ), redirect_stdout(io.StringIO()) as output:
                main()
            result = json.loads(output.getvalue())
            self.assertEqual(result["paperclip_company_id"], FLEET_COMPANY_ID)


if __name__ == "__main__":
    unittest.main()
