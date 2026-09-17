from __future__ import annotations

import json
import sqlite3

from china_trial_demo.model_screening import merge_model_results, prepare_model_jobs
from china_trial_demo.pipeline import match_batch


result = match_batch(
    "data/demo_recent.db",
    "examples/patient_demo.jsonl",
    "outputs/demo-result-one-patient.json",
    aliases_path="data/disease_ontology.json",
)
prepare_model_jobs(
    "data/demo_recent.db",
    "examples/patient_demo.jsonl",
    "outputs/demo-result-one-patient.json",
    "outputs/model-run-recent-one-patient",
    "skills/china-trial-exclusion-gater/SKILL.md",
)
merged = merge_model_results(
    "outputs/model-run-recent-one-patient",
    "outputs/final-model-screened-one-patient.json",
)
final = json.load(open("outputs/final-model-screened-one-patient.json", encoding="utf-8"))["results"][0]
connection = sqlite3.connect("data/demo_recent.db")
try:
    database = {
        "trials": connection.execute("SELECT COUNT(*) FROM trials").fetchone()[0],
        "organizations": connection.execute("SELECT COUNT(*) FROM organizations").fetchone()[0],
        "criteria": connection.execute("SELECT COUNT(*) FROM eligibility_criteria").fetchone()[0],
        "registry_ids": connection.execute("SELECT COUNT(*) FROM trial_registry_ids").fetchone()[0],
        "non_chictr_china": connection.execute(
            "SELECT COUNT(*) FROM trials WHERE chictr_registration_number IS NULL AND (countries LIKE '%China%' OR countries LIKE '%中国%')"
        ).fetchone()[0],
        "registration_date_range": connection.execute(
            "SELECT MIN(registration_date), MAX(registration_date) FROM trials"
        ).fetchone(),
    }
finally:
    connection.close()
print(json.dumps({"database": database, "local_timing_ms": final["timing_ms"], "final_summary": final["summary"], "merge": merged}, ensure_ascii=False, indent=2))
