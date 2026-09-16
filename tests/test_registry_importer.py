from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from china_trial_demo.pipeline import match_patient
from china_trial_demo.registry_importer import import_chictr_xml, import_mcp_snapshot


class RegistryImporterTests(unittest.TestCase):
  def test_import_chictr_xml_preserves_source_and_children(self) -> None:
    with tempfile.TemporaryDirectory() as directory:
      tmp_path = Path(directory)
      xml_path = tmp_path / "trial.xml"
      xml_path.write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<trials><trial><main>
<trial_id>ChiCTR2600000001</trial_id><reg_name>ChiCTR</reg_name>
<date_registration>2026-08-30</date_registration><primary_sponsor>示例医院</primary_sponsor>
<public_title>晚期肺癌试验</public_title><scientific_title>科学标题</scientific_title>
<date_enrolment>2026-09-01</date_enrolment><target_size>队列A:20;队列B:10</target_size>
<recruitment_status>Recruiting</recruitment_status><url>https://www.chictr.org.cn/showproj.html?proj=1</url>
<study_type>Interventional</study_type><study_design>开放标签</study_design><phase>Phase 2</phase>
<hc_freetext>晚期肺癌</hc_freetext><i_freetext>队列A:药物A;队列B:药物B</i_freetext>
</main><contacts><contact><type>public</type><firstname>明</firstname><lastname>李</lastname>
<affiliation>示例医院</affiliation><country1>China</country1><city>上海</city></contact></contacts>
<countries><country2>China</country2></countries><criteria>
<inclusion_criteria>1. 年龄18至75岁\n2. 经病理确诊肺癌</inclusion_criteria>
<exclusion_criteria>1. 妊娠期女性\n2. 活动性感染</exclusion_criteria>
<agemin>18 Years</agemin><agemax>75 Years</agemax><gender>Both</gender>
</criteria></trial></trials>""",
          encoding="utf-8",
      )
      db_path = tmp_path / "xml.db"
      counts = import_chictr_xml(db_path, xml_path)
      self.assertEqual(counts, {"trials": 1, "criteria": 4, "contacts": 1, "countries": 1, "cohorts": 2})
      connection = sqlite3.connect(db_path)
      try:
        trial = connection.execute("SELECT partner_trial_id,countries,raw_json FROM trials").fetchone()
        self.assertEqual(trial[0], "ChiCTR2600000001")
        self.assertEqual(trial[1], "China")
        self.assertIn("WHO_ICTRP_XML", trial[2])
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM trial_registry_ids").fetchone()[0], 1)
        self.assertEqual(connection.execute("SELECT COUNT(*) FROM trial_interventions").fetchone()[0], 2)
      finally:
        connection.close()


  def test_mcp_nct_trial_with_china_country_is_in_matching_universe(self) -> None:
    with tempfile.TemporaryDirectory() as directory:
      tmp_path = Path(directory)
      snapshot = {
        "metadata": {"database_as_of": "2026-08-18T16:59:00Z"},
        "details": [{
            "found": True,
            "primary_registry_id": "NCT09999999",
            "primary_source": "ClinicalTrials.gov",
            "title": "Recruiting study for lung cancer",
            "recruitment_status_normalized": "Recruiting",
            "study_type_normalized": "Interventional",
            "disease_text": "lung cancer",
            "countries": "China",
            "sponsor_summary": "示例申办方",
            "source_url": "https://clinicaltrials.gov/study/NCT09999999",
            "criteria": [{"criterion_type": "inclusion", "criterion_order": 1,
                          "criterion_text": "Age 18 years or older", "language": "en"}],
            "country_records": [{"country": "China", "evidence_type": "registry_country_list"}],
        }],
      }
      snapshot_path = tmp_path / "mcp.json"
      snapshot_path.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
      db_path = tmp_path / "mcp.db"
      counts = import_mcp_snapshot(db_path, snapshot_path)
      self.assertEqual(counts["trials"], 1)
      self.assertEqual(counts["country_records"], 1)

      aliases_path = tmp_path / "aliases.json"
      aliases_path.write_text(json.dumps({"groups": [["肺癌", "lung cancer"]]}, ensure_ascii=False), encoding="utf-8")
      result = match_patient(
        db_path,
        {"patient_id": "P-1", "age": 50, "sex": "男", "cancer_type": "肺癌"},
        aliases_path=aliases_path,
      )
      potential = result["potential_trials"]["non_partner"]
      self.assertEqual(len(potential), 1)
      self.assertEqual(potential[0]["registry_id"], "NCT09999999")
      self.assertIsNone(potential[0]["chictr_registration_number"])


if __name__ == "__main__":
    unittest.main()
