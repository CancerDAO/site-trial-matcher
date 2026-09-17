import json
import tempfile
import unittest
from pathlib import Path

from china_trial_demo.database_refresh import refresh_database, validate_database
from china_trial_demo.database import connect


def snapshot_payload(primary_id: str = "NCT-REFRESH-001") -> dict:
    return {
        "metadata": {"database_as_of": "2026-09-18T00:00:00Z"},
        "selection": {"selection_complete": True},
        "details": [{
            "found": True,
            "primary_registry_id": primary_id,
            "primary_source": "ClinicalTrials.gov",
            "registry_ids": [{
                "registry_source": "ClinicalTrials.gov", "registry_id": primary_id,
                "id_type": "main_id", "is_primary": True,
            }],
            "title": "Recruiting lung cancer study",
            "recruitment_status_normalized": "recruiting",
            "study_type_normalized": "interventional",
            "phase_normalized": "Phase 2",
            "disease_text": "non-small cell lung cancer",
            "disease_normalized": "NSCLC",
            "countries": "China",
            "sponsor_summary": "Refresh Test Sponsor",
            "criteria": [{
                "criterion_type": "inclusion", "criterion_order": 1,
                "criterion_text": "Age 18 years or older", "language": "en",
            }],
        }],
        "failures": {},
    }


class DatabaseRefreshTests(unittest.TestCase):
    def test_refresh_reuses_snapshot_and_atomically_replaces_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "snapshot.json"
            database = root / "trials.db"
            snapshot.write_text(json.dumps(snapshot_payload()), encoding="utf-8")

            result = refresh_database(
                database, snapshot, fetch=False, min_trials=1,
            )

            self.assertEqual(result["validation"]["trials"], 1)
            self.assertEqual(result["validation"]["criteria"], 1)
            self.assertEqual(validate_database(database)["integrity"], "ok")
            with connect(database, readonly=True) as connection:
                stored_snapshot = connection.execute(
                    "SELECT value FROM metadata WHERE key='mcp_snapshot_file'"
                ).fetchone()[0]
            self.assertEqual(stored_snapshot, str(snapshot.resolve()))

    def test_failed_validation_keeps_existing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            snapshot = root / "snapshot.json"
            database = root / "trials.db"
            database.write_bytes(b"existing-production-database")
            snapshot.write_text(json.dumps(snapshot_payload()), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "below required minimum"):
                refresh_database(database, snapshot, fetch=False, min_trials=2)

            self.assertEqual(database.read_bytes(), b"existing-production-database")


if __name__ == "__main__":
    unittest.main()
