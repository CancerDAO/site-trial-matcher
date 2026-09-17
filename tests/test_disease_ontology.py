import unittest
from pathlib import Path

from china_trial_demo.normalization import load_aliases


ROOT = Path(__file__).resolve().parents[1]


class DiseaseOntologyTests(unittest.TestCase):
    def setUp(self):
        self.ontology = load_aliases(ROOT / "data" / "disease_ontology.json")

    def test_versioned_catalog_has_stable_unique_ids(self):
        self.assertEqual(self.ontology.schema_version, "cancerdao-disease-ontology-v1")
        self.assertEqual(self.ontology.ontology_version, "2026-09-18")
        self.assertEqual(len(self.ontology.by_id), len(self.ontology.concepts))

    def test_hierarchy_compatibility_does_not_use_shared_root(self):
        self.assertTrue(self.ontology.related("aml", "leukemia"))
        self.assertTrue(self.ontology.related("breast-cancer", "solid-tumor"))
        self.assertFalse(self.ontology.related("breast-cancer", "lung-cancer"))

    def test_composite_breast_pathology_maps_to_breast(self):
        self.assertEqual(
            self.ontology.match("乳腺浸润性导管癌"),
            {"breast-cancer"},
        )


if __name__ == "__main__":
    unittest.main()
