import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from china_trial_demo.database import connect
from china_trial_demo.demo_builder import build_demo
from china_trial_demo.pipeline import match_batch

ROOT = Path(__file__).resolve().parents[1]


class PipelineTests(unittest.TestCase):
  def test_duplicate_patient_ids_are_rejected(self):
    with tempfile.TemporaryDirectory() as directory:
      root = Path(directory); patients = root / "patients.jsonl"; output = root / "out.json"
      patients.write_text('{"patient_id":"P1","cancer_type":"肺癌"}\n{"patient_id":"P1","cancer_type":"肺癌"}\n', encoding="utf-8")
      with self.assertRaisesRegex(ValueError, "重复patient_id"):
        match_batch(root / "missing.db", patients, output, aliases_path=ROOT / "data" / "cancer_aliases.json")
  def test_nsclc_does_not_expand_to_sclc(self):
    from china_trial_demo.normalization import expand_terms
    from china_trial_demo.retrieval import _group_ids
    groups = [["非小细胞肺癌", "NSCLC", "non-small cell lung cancer"], ["小细胞肺癌", "SCLC", "small cell lung cancer"]]
    self.assertEqual(expand_terms(["非小细胞肺癌"], groups), groups[0])
    self.assertEqual(_group_ids("非小细胞肺癌 NSCLC", groups), {0})
    self.assertEqual(_group_ids("小细胞肺癌 SCLC", groups), {1})

  def test_qualified_solid_tumor_wording_is_broad(self):
    from china_trial_demo.retrieval import _is_broad_disease
    self.assertTrue(_is_broad_disease("metastatic refractory solid tumors"))
    self.assertTrue(_is_broad_disease("复发难治性实体瘤"))
    self.assertFalse(_is_broad_disease("转移性乳腺癌"))

  def test_multi_population_solid_tumor_trial_is_not_excluded_by_lymphoma_arm(self):
    from china_trial_demo.retrieval import disease_assessment
    groups = [["非小细胞肺癌", "NSCLC"], ["淋巴瘤", "lymphoma"]]
    trial = {"primary_disease": "Malignancies | NHL", "disease_aliases": "",
             "title_en": "Advanced solid tumors or relapsed lymphoma", "title_zh": ""}
    patient = {"cancer_type": "NSCLC", "disease_terms": ["NSCLC"]}
    self.assertFalse(disease_assessment(patient, trial, groups)["hard_mismatch"])

  def test_batch_runtime_and_buckets(self):
    with tempfile.TemporaryDirectory() as directory:
      tmp_path = Path(directory)
      source = tmp_path / "source.db"
      conn = sqlite3.connect(source)
      conn.execute("CREATE TABLE trials(registration_number TEXT, institution TEXT, detail_json TEXT)")
      for index in range(25):
        detail = {
            "registration_number": f"ChiCTR2600{index:06d}", "title": "EGFR突变非小细胞肺癌研究", "title_en": "EGFR NSCLC",
            "disease": "非小细胞肺癌", "disease_en": "NSCLC", "objectives": "EGFR", "objectives_en": "",
            "recruitment_status": "尚未开始", "study_type": "干预性研究", "study_phase": "II期", "interventions": [],
            "inclusion_criteria": "1. 年龄18-75岁；\n2. ECOG 0-1分", "inclusion_criteria_en": "",
            "exclusion_criteria": "活动性感染", "exclusion_criteria_en": "",
            "primary_sponsor": "四川大学华西医院", "leader_institution": "四川大学华西医院",
            "study_start_date": "", "study_end_date": "", "detail_url": f"https://www.chictr.org.cn/showproj.html?proj={index}", "scraped_at": "2026-08-01",
        }
        conn.execute("INSERT INTO trials VALUES(?,?,?)", (detail["registration_number"], "四川大学华西医院", json.dumps(detail, ensure_ascii=False)))
      broad = dict(detail)
      broad.update({"registration_number": "ChiCTR2600999999", "title": "晚期实体瘤研究", "disease": "晚期实体瘤",
                    "disease_en": "solid tumor", "inclusion_criteria": "经病理确认的恶性肿瘤", "exclusion_criteria": "原资料未提供",
                    "detail_url": "https://www.chictr.org.cn/showproj.html?proj=broad"})
      conn.execute("INSERT INTO trials VALUES(?,?,?)", (broad["registration_number"], "四川大学华西医院", json.dumps(broad, ensure_ascii=False)))
      breast = dict(detail)
      breast.update({"registration_number": "ChiCTR2600888888", "title": "乳腺癌研究", "title_en": "breast cancer study", "disease": "乳腺癌", "disease_en": "breast cancer",
                     "inclusion_criteria": "经病理确认的乳腺癌", "detail_url": "https://www.chictr.org.cn/showproj.html?proj=breast"})
      conn.execute("INSERT INTO trials VALUES(?,?,?)", (breast["registration_number"], "四川大学华西医院", json.dumps(breast, ensure_ascii=False)))
      conn.commit()
      conn.close()
      top30 = tmp_path / "top30.json"
      top30.write_text(json.dumps({"source_url": "https://www.chictr.org.cn/sponsorproj.html", "fetched_at": "2026-08-27T00:00:00+08:00", "institutions": [{"rank": 1, "organization_code": "TOP-01", "name_zh": "四川大学华西医院", "trial_count": 25}]}, ensure_ascii=False), encoding="utf-8")
      aliases = tmp_path / "aliases.json"
      aliases.write_text(json.dumps({"groups": [["非小细胞肺癌", "NSCLC", "肺腺癌"], ["乳腺癌", "breast cancer"]]}, ensure_ascii=False), encoding="utf-8")
      target = tmp_path / "demo.db"
      build_demo(source, target, top30)
      patients = tmp_path / "patients.jsonl"
      patients.write_text(json.dumps({"patient_id": "P1", "cancer_type": "肺腺癌", "age": 90, "ecog": 1}, ensure_ascii=False) + "\n", encoding="utf-8")
      output = tmp_path / "result.json"
      payload = match_batch(target, patients, output, aliases_path=aliases)
      self.assertEqual(payload["patient_count"], 1)
      self.assertLess(payload["average_patient_ms"], 1_200_000)
      result = payload["results"][0]
      self.assertEqual(result["trial_universe_count"], 27)
      self.assertEqual(result["summary"]["excluded_count"], 26)
      self.assertEqual(result["summary"]["potential_count"], 1)
      self.assertEqual(result["potential_trials"]["partner"][0]["chictr_registration_number"], "ChiCTR2600999999")
      breast_result = next(item for item in result["excluded_trials"]["partner"] if item["chictr_registration_number"] == "ChiCTR2600888888")
      self.assertTrue(any(reason.get("reason_code") == "DISEASE_MISMATCH" for reason in breast_result["exclusion_reasons"]))


if __name__ == "__main__":
    unittest.main()
