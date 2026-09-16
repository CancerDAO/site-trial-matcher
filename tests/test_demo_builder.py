import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from china_trial_demo.database import connect
from china_trial_demo.demo_builder import build_demo, strict_structure


class DemoBuilderTests(unittest.TestCase):
  def test_strict_structure_parses_common_english_age_and_ecog(self):
    self.assertEqual(strict_structure("Subjects aged 18 to 75 years", criterion_type="inclusion", disease="Cancer"), ("age", "between", "18|75", "year"))
    self.assertEqual(strict_structure("Age 18 years or older", criterion_type="inclusion", disease="Cancer"), ("age", "gte", "18", "year"))
    self.assertEqual(strict_structure("Eastern Cooperative Oncology Group (ECOG) performance status of 0-2", criterion_type="inclusion", disease="Cancer"), ("ecog", "lte", "2", "score"))
  def test_build_demo_imports_only_detailed_cancer_trials_and_marks_top30(self):
    with tempfile.TemporaryDirectory() as directory:
      tmp_path = Path(directory)
      source = tmp_path / "source.db"
      conn = sqlite3.connect(source)
      conn.execute("CREATE TABLE trials(registration_number TEXT, institution TEXT, detail_json TEXT)")
      detail = {
        "registration_number": "ChiCTR2600000001", "title": "肺癌研究", "title_en": "lung cancer",
        "disease": "非小细胞肺癌", "disease_en": "NSCLC", "objectives": "", "objectives_en": "",
        "recruitment_status": "尚未开始", "study_type": "干预性研究", "study_phase": "II期",
        "interventions": [], "inclusion_criteria": "1. 年龄18-75岁；\n2. ECOG 0-1分",
        "inclusion_criteria_en": "", "exclusion_criteria": "活动性感染", "exclusion_criteria_en": "",
        "primary_sponsor": "四川大学华西医院", "leader_institution": "四川大学华西医院",
        "study_start_date": "", "study_end_date": "", "detail_url": "https://www.chictr.org.cn/showproj.html?proj=1",
        "scraped_at": "2026-08-01", "title_zh": "", "registration_status": "预注册",
    }
      conn.execute("INSERT INTO trials VALUES(?,?,?)", (detail["registration_number"], "四川大学华西医院", json.dumps(detail, ensure_ascii=False)))
      conn.execute("INSERT INTO trials VALUES(?,?,?)", ("ChiCTR2600000002", "其他医院", None))
      conn.commit()
      conn.close()
      top30 = tmp_path / "top30.json"
      top30.write_text(json.dumps({"source_url": "https://www.chictr.org.cn/sponsorproj.html", "fetched_at": "2026-08-27T00:00:00+08:00", "institutions": [{"rank": 1, "organization_code": "TOP-01", "name_zh": "四川大学华西医院", "trial_count": 1}]}, ensure_ascii=False), encoding="utf-8")
      target = tmp_path / "demo.db"
      report = build_demo(source, target, top30)
      self.assertEqual(report["snapshot_trials_scanned"], 2)
      self.assertEqual(report["detail_trials_scanned"], 1)
      self.assertEqual(report["snapshot_trials_imported"], 1)
      self.assertEqual(report["top30_partner_trials"], 1)
      self.assertEqual(report["non_partner_trials"], 0)
      with connect(target, readonly=True) as db:
          self.assertEqual(db.execute("SELECT COUNT(*) FROM trials").fetchone()[0], 1)
          self.assertEqual(db.execute("SELECT COUNT(*) FROM trials t JOIN organizations o ON o.id=t.organization_id WHERE o.partner_status='demo_partner'").fetchone()[0], 1)
          rows = db.execute("SELECT structured_field,operator,structured_value FROM eligibility_criteria WHERE criterion_type='inclusion'").fetchall()
      self.assertTrue(any(row[0] == "age" and row[1] == "between" for row in rows))
      self.assertTrue(any(row[0] == "ecog" and row[2] == "1" for row in rows))


if __name__ == "__main__":
    unittest.main()
