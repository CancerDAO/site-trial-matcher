import unittest

from china_trial_demo.eligibility import evaluate_trial


def criterion(kind, identifier, field, operator, value):
    return {
        "criterion_type": kind,
        "criterion_id": identifier,
        "criterion_category": field,
        "criterion_text_zh": identifier,
        "is_mandatory": 1,
        "structured_field": field,
        "operator": operator,
        "structured_value": value,
    }


class EligibilityTests(unittest.TestCase):
    def test_sex_comparison_is_bilingual(self):
        result = evaluate_trial(
            {"sex": "女"},
            {"id": 1, "title_zh": "T", "chictr_registration_number": "ChiCTR1", "sex": "Female"},
            [],
        )
        self.assertEqual(result["disposition"], "no_deterministic_exclusion")

    def test_explicit_inclusion_failure_excludes(self):
        result = evaluate_trial(
            {"age": 80},
            {"id": 1, "title_zh": "T", "chictr_registration_number": "ChiCTR1"},
            [criterion("inclusion", "AGE", "age", "lte", "75")],
        )
        self.assertEqual(result["disposition"], "excluded")
        self.assertEqual(result["exclusion_reasons"][0]["criterion_id"], "AGE")

    def test_explicit_exclusion_trigger_excludes(self):
        result = evaluate_trial(
            {"comorbidities": ["未控制的活动性感染"]},
            {"id": 1, "title_zh": "T", "chictr_registration_number": "ChiCTR1"},
            [criterion("exclusion", "INF", "comorbidities", "contains", "活动性感染")],
        )
        self.assertEqual(result["disposition"], "excluded")

    def test_missing_fact_is_needs_review_not_exclusion(self):
        result = evaluate_trial(
            {},
            {"id": 1, "title_zh": "T", "chictr_registration_number": "ChiCTR1"},
            [criterion("exclusion", "INF", "comorbidities", "contains", "活动性感染")],
        )
        self.assertEqual(result["disposition"], "needs_review")
        self.assertFalse(result["exclusion_reasons"])


if __name__ == "__main__":
    unittest.main()
