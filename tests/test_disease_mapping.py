import unittest
from pathlib import Path
from unittest.mock import patch

from china_trial_demo.disease_mapping import map_patients_with_model
from china_trial_demo.normalization import DiseaseMappingError, load_aliases


ROOT = Path(__file__).resolve().parents[1]


class DiseaseMappingTests(unittest.TestCase):
    def setUp(self):
        self.ontology = load_aliases(ROOT / "data" / "disease_ontology.json")
        self.patient = {
            "patient_id": "P-1",
            "cancer_type": "复杂乳腺病理描述",
            "histology": "乳腺浸润性导管癌",
        }

    @patch("china_trial_demo.disease_mapping.call_minimax_prompt")
    def test_model_selects_only_catalog_concept_with_verbatim_evidence(self, call):
        call.return_value = ({"mappings": [{
            "case_id": "case-0001",
            "status": "mapped",
            "concept_id": "breast-cancer",
            "confidence": 0.94,
            "evidence": {"patient_field": "histology", "patient_text": "乳腺浸润性导管癌"},
            "candidate_ids": [],
        }]}, {"model": "test", "usage": {}})
        rows, meta = map_patients_with_model([self.patient], self.ontology)
        self.assertEqual(rows[0]["canonical_disease_id"], "breast-cancer")
        self.assertEqual(rows[0]["disease_mapping_method"], "model_constrained")
        self.assertEqual(meta["model"], "test")

    @patch("china_trial_demo.disease_mapping.call_minimax_prompt")
    def test_model_cannot_use_paraphrased_evidence(self, call):
        call.return_value = ({"mappings": [{
            "case_id": "case-0001", "status": "mapped", "concept_id": "breast-cancer",
            "confidence": 0.95,
            "evidence": {"patient_field": "histology", "patient_text": "浸润性乳腺癌"},
            "candidate_ids": [],
        }]}, {})
        with self.assertRaisesRegex(DiseaseMappingError, "invalid_model_disease_evidence"):
            map_patients_with_model([self.patient], self.ontology)

    @patch("china_trial_demo.disease_mapping.call_minimax_prompt")
    def test_model_abstention_stops_mapping(self, call):
        call.return_value = ({"mappings": [{
            "case_id": "case-0001", "status": "unmapped", "concept_id": None,
            "confidence": 0.0, "evidence": None, "candidate_ids": [],
        }]}, {})
        with self.assertRaisesRegex(DiseaseMappingError, "unmapped_disease"):
            map_patients_with_model([self.patient], self.ontology)


if __name__ == "__main__":
    unittest.main()
