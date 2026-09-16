import unittest
import json
import tempfile
from pathlib import Path

from china_trial_demo.model_api import extract_json, load_skill
from china_trial_demo.model_screening import _coalesce_source_fragments, merge_model_results, validate_model_output


ROOT = Path(__file__).resolve().parents[1]


def job():
    return {
        "expected_trial_ids": ["ChiCTR1"],
        "patient": {"patient_id": "P1", "cancer_type": "非小细胞肺癌", "age": 56},
        "trials": [{"trial_id": "ChiCTR1", "title": "乳腺癌研究", "title_en": "", "primary_disease": "乳腺癌",
                    "disease_aliases": "", "inclusion": [], "exclusion": []}],
    }


class ModelScreeningTests(unittest.TestCase):
    def test_wrapped_source_fragments_are_rebuilt_verbatim(self):
        rows = [
            {"criterion_type": "inclusion", "cohort_id": "ALL", "criterion_id": "INC-1", "criterion_text_zh": "There must be no EGFR gene-sensitive mutation, ALK gene fusion or ROS1 gene fusion"},
            {"criterion_type": "inclusion", "cohort_id": "ALL", "criterion_id": "INC-2", "criterion_text_zh": "in non-squamous carcinoma"},
            {"criterion_type": "inclusion", "cohort_id": "ALL", "criterion_id": "INC-3", "criterion_text_zh": "Age 18 years or older."},
        ]
        rebuilt = _coalesce_source_fragments(rows)
        self.assertEqual(len(rebuilt), 2)
        self.assertEqual(rebuilt[0]["criterion_text_zh"], "There must be no EGFR gene-sensitive mutation, ALK gene fusion or ROS1 gene fusion in non-squamous carcinoma")
    def test_skill_loads_linked_policy_and_contract(self):
        text = load_skill(ROOT / "skills" / "china-trial-exclusion-gater" / "SKILL.md")
        self.assertIn("排除判断政策", text)
        self.assertIn("analyzed_trials", text)
        self.assertIn("不能代替血小板减少", text)

    def test_extract_json_after_reasoning_text(self):
        payload = extract_json("<think>分析</think>\n{\"analyzed_trials\": []}")
        self.assertEqual(payload, {"analyzed_trials": []})

    def test_valid_scope_exclusion_restores_patient_value(self):
        payload = {"analyzed_trials": [{"trial_id": "ChiCTR1", "disposition": "exclude", "exclusion_basis": "scope_mismatch",
            "confidence": 0.95, "evidence": {"source_kind": "trial_scope", "trial_text": "乳腺癌", "patient_field": "cancer_type",
                "assessment": "明确异癌种", "certainty": "clear", "cohort_id": "ALL"}, "review_note": ""}]}
        result = validate_model_output(job(), payload)
        self.assertEqual(result["analyzed_trials"][0]["evidence"]["patient_value"], "非小细胞肺癌")

    def test_verbatim_title_fragment_is_bound(self):
        task = job(); task["trials"][0]["title"] = "乳腺癌研究 一项前瞻性临床研究"
        payload = {"analyzed_trials": [{"trial_id": "ChiCTR1", "disposition": "exclude", "exclusion_basis": "scope_mismatch",
            "confidence": 0.95, "evidence": {"source_kind": "trial_scope", "trial_text": "乳腺癌研究", "patient_field": "cancer_type",
                "assessment": "明确异癌种", "certainty": "clear", "cohort_id": "ALL"}, "review_note": ""}]}
        result = validate_model_output(task, payload)
        self.assertEqual(result["analyzed_trials"][0]["disposition"], "exclude")

    def test_trivial_ascii_fragment_is_not_sufficient_evidence(self):
        task = job(); task["trials"][0]["title"] = "Breast cancer clinical research"
        payload = {"analyzed_trials": [{"trial_id": "ChiCTR1", "disposition": "exclude", "exclusion_basis": "scope_mismatch",
            "confidence": 0.95, "evidence": {"source_kind": "trial_scope", "trial_text": "cancer", "patient_field": "cancer_type",
                "assessment": "异癌种", "certainty": "clear", "cohort_id": "ALL"}, "review_note": ""}]}
        with self.assertRaises(ValueError):
            validate_model_output(task, payload)

    def test_paraphrased_trial_text_is_rejected(self):
        payload = {"analyzed_trials": [{"trial_id": "ChiCTR1", "disposition": "exclude", "exclusion_basis": "scope_mismatch",
            "confidence": 0.95, "evidence": {"source_kind": "trial_scope", "trial_text": "一个乳腺肿瘤项目", "patient_field": "cancer_type",
                "assessment": "明确异癌种", "certainty": "clear", "cohort_id": "ALL"}, "review_note": ""}]}
        with self.assertRaises(ValueError):
            validate_model_output(job(), payload)

    def test_exclusion_with_missing_patient_fact_is_rejected(self):
        payload = {"analyzed_trials": [{"trial_id": "ChiCTR1", "disposition": "exclude", "exclusion_basis": "scope_mismatch",
            "confidence": 0.9, "evidence": {"source_kind": "trial_scope", "trial_text": "乳腺癌", "patient_field": "brain_mri",
                "assessment": "无检查", "certainty": "clear", "cohort_id": "ALL"}, "review_note": ""}]}
        with self.assertRaises(ValueError):
            validate_model_output(job(), payload)

    def test_scope_exclusion_cannot_infer_resistance_from_prior_therapy(self):
        task = job(); task["patient"]["prior_therapies"] = ["奥希替尼"]
        payload = {"analyzed_trials": [{"trial_id": "ChiCTR1", "disposition": "exclude", "exclusion_basis": "scope_mismatch",
            "confidence": 0.95, "evidence": {"source_kind": "trial_scope", "trial_text": "乳腺癌", "patient_field": "prior_therapies",
                "assessment": "未报告耐药", "certainty": "clear", "cohort_id": "ALL"}, "review_note": ""}]}
        with self.assertRaises(ValueError):
            validate_model_output(task, payload)

    def test_supportive_care_trial_cannot_be_scope_excluded(self):
        task = job(); task["trials"][0].update({"title": "癌症患者围手术期焦虑干预", "primary_disease": "围手术期焦虑"})
        payload = {"analyzed_trials": [{"trial_id": "ChiCTR1", "disposition": "exclude", "exclusion_basis": "scope_mismatch",
            "confidence": 0.95, "evidence": {"source_kind": "trial_scope", "trial_text": "围手术期焦虑", "patient_field": "cancer_type",
                "assessment": "范围不同", "certainty": "clear", "cohort_id": "ALL"}, "review_note": ""}]}
        with self.assertRaisesRegex(ValueError, "支持/症状管理"):
            validate_model_output(task, payload)

    def test_retain_allows_null_confidence(self):
        payload = {"analyzed_trials": [{"trial_id": "ChiCTR1", "disposition": "retain_for_review", "exclusion_basis": None,
            "confidence": None, "evidence": None, "review_note": "详情不足"}]}
        result = validate_model_output(job(), payload)
        self.assertEqual(result["analyzed_trials"][0]["confidence"], 0.0)

    def test_merge_moves_evidence_bound_model_exclusion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); jobs = root / "jobs"; results = root / "results"; jobs.mkdir(); results.mkdir()
            deterministic_path = root / "deterministic.json"
            deterministic_path.write_text(json.dumps({"schema_version": "v2", "patient_count": 1, "results": [{"patient_id": "P1",
                "potential_trials": {"partner": [{"trial_id": 1, "chictr_registration_number": "ChiCTR1", "partner_status": "demo_partner"}], "non_partner": []},
                "excluded_trials": {"partner": [], "non_partner": []}, "summary": {"potential_count": 1, "excluded_count": 0,
                    "partner_potential_count": 1, "non_partner_potential_count": 0, "partner_excluded_count": 0, "non_partner_excluded_count": 0}}]}, ensure_ascii=False), encoding="utf-8")
            job_path = jobs / "P1-scope-0001.json"; result_path = results / "P1-scope-0001.json"
            task = {**job(), "job_id": "P1-scope-0001", "mode": "scope"}; job_path.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
            result_path.write_text(json.dumps({"analyzed_trials": [{"trial_id": "ChiCTR1", "disposition": "exclude", "exclusion_basis": "scope_mismatch",
                "confidence": 0.95, "evidence": {"source_kind": "trial_scope", "trial_text": "乳腺癌", "patient_field": "cancer_type",
                    "assessment": "明确异癌种", "certainty": "clear", "cohort_id": "ALL"}, "review_note": ""}]}, ensure_ascii=False), encoding="utf-8")
            manifest = {"deterministic_results_path": str(deterministic_path), "job_count": 1, "trial_assignments": 1,
                "jobs": [{"patient_id": "P1", "job_path": str(job_path), "result_path": str(result_path)}]}
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            output = root / "merged.json"; merge_model_results(root, output); merged = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(merged["results"][0]["summary"]["excluded_count"], 1)
            self.assertEqual(merged["results"][0]["excluded_trials"]["partner"][0]["disposition"], "model_supported_exclusion")

    def test_merge_retains_trial_when_model_result_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); jobs = root / "jobs"; results = root / "results"; jobs.mkdir(); results.mkdir()
            deterministic_path = root / "deterministic.json"
            deterministic_path.write_text(json.dumps({"schema_version": "v2", "patient_count": 1, "results": [{"patient_id": "P1",
                "potential_trials": {"partner": [], "non_partner": [{"trial_id": 1, "chictr_registration_number": "ChiCTR1"}]},
                "excluded_trials": {"partner": [], "non_partner": []}, "summary": {"potential_count": 1, "excluded_count": 0,
                    "partner_potential_count": 0, "non_partner_potential_count": 1, "partner_excluded_count": 0, "non_partner_excluded_count": 0}}]}, ensure_ascii=False), encoding="utf-8")
            job_path = jobs / "P1-scope-0001.json"; result_path = results / "P1-scope-0001.json"
            task = {**job(), "job_id": "P1-scope-0001", "mode": "scope"}; job_path.write_text(json.dumps(task, ensure_ascii=False), encoding="utf-8")
            manifest = {"deterministic_results_path": str(deterministic_path), "job_count": 1, "trial_assignments": 1,
                "jobs": [{"patient_id": "P1", "job_path": str(job_path), "result_path": str(result_path), "expected_trial_ids": ["ChiCTR1"]}]}
            (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            output = root / "merged.json"; merge_model_results(root, output); merged = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(merged["results"][0]["summary"]["potential_count"], 1)
            screening = merged["results"][0]["potential_trials"]["non_partner"][0]["model_screening"]
            self.assertEqual(screening["disposition"], "retain_for_review")
            self.assertEqual(merged["model_run"]["fallback_job_count"], 1)


if __name__ == "__main__":
    unittest.main()
