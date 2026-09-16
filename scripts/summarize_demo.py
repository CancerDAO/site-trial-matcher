from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--results", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    connection = sqlite3.connect(f"file:{args.db.resolve().as_posix()}?mode=ro", uri=True)
    database = {
        "organizations": connection.execute("SELECT COUNT(*) FROM organizations").fetchone()[0],
        "organizations_with_trials": connection.execute("SELECT COUNT(DISTINCT organization_id) FROM trials").fetchone()[0],
        "trials": connection.execute("SELECT COUNT(*) FROM trials").fetchone()[0],
        "active_trials": connection.execute("SELECT COUNT(*) FROM trials WHERE recruitment_status IN ('尚未开始','招募中','未知') AND record_status='有效'").fetchone()[0],
        "criteria": connection.execute("SELECT COUNT(*) FROM eligibility_criteria").fetchone()[0],
        "structured_criteria": connection.execute("SELECT COUNT(*) FROM eligibility_criteria WHERE structured_field IS NOT NULL AND operator IS NOT NULL").fetchone()[0],
        "inclusion_criteria": connection.execute("SELECT COUNT(*) FROM eligibility_criteria WHERE criterion_type='inclusion'").fetchone()[0],
        "exclusion_criteria": connection.execute("SELECT COUNT(*) FROM eligibility_criteria WHERE criterion_type='exclusion'").fetchone()[0],
    }
    connection.close()
    payload = json.loads(args.results.read_text(encoding="utf-8"))
    patients = []
    for result in payload["results"]:
        all_items = [item for section in (result["potential_trials"], result["excluded_trials"]) for items in section.values() for item in items]
        dispositions = Counter(item["disposition"] for item in all_items)
        patients.append({
            "patient_id": result["patient_id"],
            "trial_universe_count": result["trial_universe_count"],
            "counts": result["summary"],
            "dispositions": dict(dispositions),
            "timing_ms": result["timing_ms"],
        })
    summary = {
        "database": database,
        "batch": {
            "patient_count": payload["patient_count"],
            "total_ms": payload["total_ms"],
            "average_patient_ms": payload["average_patient_ms"],
        },
        "patients": patients,
    }
    text = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
