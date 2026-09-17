import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    from fastapi.testclient import TestClient
    from china_trial_demo.database import initialize
    from china_trial_demo.web import create_app
    from china_trial_demo.web_service import WebSettings
except ImportError:
    TestClient = None


@unittest.skipIf(TestClient is None, "web test dependencies are not installed")
class WebApiTests(unittest.TestCase):
    def test_dataset_create_poll_and_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / "trials.db"
            initialize(db)
            aliases = root / "aliases.json"
            aliases.write_text('{"groups":[["肺癌","lung cancer"]]}', encoding="utf-8")
            skill = root / "SKILL.md"
            skill.write_text("test", encoding="utf-8")
            settings = WebSettings(
                project_root=Path(__file__).resolve().parents[1],
                data_root=root / "var",
                db_path=db,
                snapshot_path=root / "missing.json",
                aliases_path=aliases,
                skill_path=skill,
                model_enabled=False,
                model_workers=1,
                task_workers=1,
                retention_hours=24,
            )
            with TestClient(create_app(settings)) as client:
                self.assertEqual(client.get("/healthz").status_code, 200)
                dataset = client.get("/api/dataset").json()
                self.assertEqual(dataset["counts"]["trials"], 0)
                concepts = client.get("/api/disease-concepts").json()
                self.assertEqual(concepts["ontology_version"], "legacy")
                self.assertEqual(concepts["concepts"][0]["label"], "肺癌")
                response = client.post("/api/runs", json={
                    "mode": "deterministic",
                    "patients": [{"patient_id": "WEB-1", "cancer_type": "肺癌", "age": 58}],
                })
                self.assertEqual(response.status_code, 202, response.text)
                run_id = response.json()["run_id"]
                deadline = time.time() + 5
                state = {}
                while time.time() < deadline:
                    state = client.get(f"/api/runs/{run_id}").json()
                    if state.get("status") in {"completed", "failed"}:
                        break
                    time.sleep(0.02)
                self.assertEqual(state.get("status"), "completed")
                results = client.get(f"/api/runs/{run_id}/results").json()
                self.assertEqual(results["patient_count"], 1)
                self.assertEqual(results["results"][0]["patient_id"], "WEB-1")
                detail = client.get(f"/api/runs/{run_id}/results/0").json()
                self.assertEqual(detail["result"]["patient_id"], "WEB-1")

    def test_create_rejects_unmapped_disease_without_starting_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / "trials.db"
            initialize(db)
            aliases = root / "aliases.json"
            aliases.write_text('{"groups":[["乳腺癌","breast cancer"]]}', encoding="utf-8")
            skill = root / "SKILL.md"
            skill.write_text("test", encoding="utf-8")
            settings = WebSettings(
                project_root=Path(__file__).resolve().parents[1], data_root=root / "var", db_path=db,
                snapshot_path=root / "missing.json", aliases_path=aliases, skill_path=skill,
                model_enabled=False, model_workers=1, task_workers=1, retention_hours=24,
            )
            with TestClient(create_app(settings)) as client:
                response = client.post("/api/runs", json={
                    "mode": "deterministic",
                    "patients": [{"patient_id": "WEB-UNKNOWN", "cancer_type": "未定义罕见肿瘤"}],
                })
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["detail"]["code"], "unmapped_disease")
                self.assertFalse(any((root / "var" / "states").glob("*.json")))

    def test_create_rejects_client_supplied_controlled_concept(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / "trials.db"
            initialize(db)
            aliases = root / "aliases.json"
            aliases.write_text('{"groups":[["乳腺癌","breast cancer"]]}', encoding="utf-8")
            settings = WebSettings(
                project_root=Path(__file__).resolve().parents[1], data_root=root / "var", db_path=db,
                snapshot_path=root / "missing.json", aliases_path=aliases, skill_path=root / "SKILL.md",
                model_enabled=False, model_workers=1, task_workers=1, retention_hours=24,
            )
            with TestClient(create_app(settings)) as client:
                response = client.post("/api/runs", json={
                    "patients": [{
                        "patient_id": "WEB-FORGE", "cancer_type": "肺癌",
                        "canonical_disease_id": "legacy-000",
                    }],
                })
                self.assertEqual(response.status_code, 422)

    @patch("china_trial_demo.web.map_patients_with_model")
    def test_model_mapping_fallback_adds_controlled_concept_before_run(self, mapper):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / "trials.db"
            initialize(db)
            aliases = root / "aliases.json"
            aliases.write_text('{"groups":[["乳腺癌","breast cancer"]]}', encoding="utf-8")
            skill = root / "SKILL.md"
            skill.write_text("test", encoding="utf-8")
            settings = WebSettings(
                project_root=Path(__file__).resolve().parents[1], data_root=root / "var", db_path=db,
                snapshot_path=root / "missing.json", aliases_path=aliases, skill_path=skill,
                model_enabled=True, model_workers=1, task_workers=1, retention_hours=24,
            )
            mapper.return_value = ([{
                "patient_id": "WEB-MODEL", "cancer_type": "复杂病理描述",
                "canonical_disease_id": "legacy-000", "canonical_cancer_type": "乳腺癌",
                "disease_mapping_method": "model_constrained", "disease_mapping_confidence": 0.94,
                "disease_mapping_evidence": "复杂病理描述",
            }], {"model": "test"})
            with TestClient(create_app(settings)) as client:
                response = client.post("/api/runs", json={
                    "mode": "deterministic",
                    "patients": [{"patient_id": "WEB-MODEL", "cancer_type": "复杂病理描述"}],
                })
                self.assertEqual(response.status_code, 202, response.text)
                mapper.assert_called_once()
                run_id = response.json()["run_id"]
                for _ in range(100):
                    state = client.get(f"/api/runs/{run_id}").json()
                    if state.get("status") in {"completed", "failed"}:
                        break
                    time.sleep(0.02)
                self.assertEqual(state.get("status"), "completed")

    def test_full_mode_is_closed_without_server_model_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db = root / "trials.db"
            initialize(db)
            aliases = root / "aliases.json"
            aliases.write_text('{"groups":[["肺癌","lung cancer"]]}', encoding="utf-8")
            settings = WebSettings(
                project_root=Path(__file__).resolve().parents[1], data_root=root / "var", db_path=db,
                snapshot_path=root / "missing.json", aliases_path=aliases, skill_path=root / "SKILL.md",
                model_enabled=False, model_workers=1, task_workers=1, retention_hours=24,
            )
            with TestClient(create_app(settings)) as client:
                response = client.post("/api/runs", json={
                    "mode": "full", "patients": [{"patient_id": "WEB-1", "cancer_type": "肺癌"}],
                })
                self.assertEqual(response.status_code, 409)


if __name__ == "__main__":
    unittest.main()
