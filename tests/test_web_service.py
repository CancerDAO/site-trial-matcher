import json
import tempfile
import time
import unittest
from pathlib import Path

from china_trial_demo.database import initialize
from china_trial_demo.model_screening import safe_patient_job_key
from china_trial_demo.web_service import RunManager, WebSettings


class WebServiceTests(unittest.TestCase):
    def settings(self, root: Path) -> WebSettings:
        db = root / "trials.db"
        initialize(db)
        aliases = root / "aliases.json"
        aliases.write_text('{"groups":[]}', encoding="utf-8")
        skill = root / "SKILL.md"
        skill.write_text("test", encoding="utf-8")
        return WebSettings(
            project_root=root,
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

    def wait(self, manager: RunManager, run_id: str, terminal: set[str]) -> dict:
        deadline = time.time() + 5
        while time.time() < deadline:
            state = manager.get(run_id)
            if state["status"] in terminal:
                return state
            time.sleep(0.02)
        self.fail("run did not reach terminal state")

    def test_web_run_uses_server_uuid_and_returns_results(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def matcher(db, patients_path, output_path, **kwargs):
                patients = [json.loads(line) for line in Path(patients_path).read_text(encoding="utf-8").splitlines()]
                payload = {"schema_version": "test", "results": [{"patient_id": row["patient_id"], "summary": {"potential_count": 1, "excluded_count": 0, "partner_potential_count": 0}} for row in patients]}
                Path(output_path).write_text(json.dumps(payload), encoding="utf-8")
                return payload

            manager = RunManager(self.settings(root), matcher=matcher)
            try:
                created = manager.create([{"patient_id": "../../not-a-path", "cancer_type": "肺癌"}], mode="deterministic")
                state = self.wait(manager, created["run_id"], {"completed", "failed"})
                self.assertEqual(state["status"], "completed")
                self.assertNotIn("not-a-path", str(manager._run_dir(created["run_id"])))
                results = manager.results(created["run_id"], offset=0, limit=20)
                self.assertEqual(results["results"][0]["patient_id"], "../../not-a-path")
            finally:
                manager.close()

    def test_cancelled_run_removes_clinical_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def matcher(db, patients_path, output_path, **kwargs):
                time.sleep(0.15)
                payload = {"schema_version": "test", "results": []}
                Path(output_path).write_text(json.dumps(payload), encoding="utf-8")
                return payload

            manager = RunManager(self.settings(root), matcher=matcher)
            try:
                created = manager.create([{"patient_id": "P1", "cancer_type": "肺癌"}], mode="deterministic")
                manager.cancel(created["run_id"])
                state = self.wait(manager, created["run_id"], {"cancelled", "failed"})
                self.assertEqual(state["status"], "cancelled")
                self.assertFalse(manager._run_dir(created["run_id"]).exists())
            finally:
                manager.close()

    def test_patient_identifier_never_becomes_a_path_component(self):
        key = safe_patient_job_key("../../患者/secret", 2)
        self.assertRegex(key, r"^p0002-[0-9a-f]{12}$")
        self.assertNotIn("患者", key)
        self.assertNotIn("..", key)


if __name__ == "__main__":
    unittest.main()
