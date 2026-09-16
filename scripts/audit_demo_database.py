from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path


def parsed_date(value: str | None) -> datetime:
    text = str(value or "").strip()
    for pattern in ("%Y-%m-%d", "%d/%m/%Y", "%d %B %Y", "%B %d, %Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            pass
    return datetime.min


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path)
    args = parser.parse_args()
    connection = sqlite3.connect(args.db)
    connection.row_factory = sqlite3.Row
    one = lambda sql: connection.execute(sql).fetchone()[0]
    rows = connection.execute("SELECT registration_date, primary_registry FROM trials").fetchall()
    dates = sorted((parsed_date(row["registration_date"]) for row in rows if parsed_date(row["registration_date"]) != datetime.min))
    report = {
        "database": str(args.db.resolve()),
        "trials": one("SELECT COUNT(*) FROM trials"),
        "unique_trial_ids": one("SELECT COUNT(DISTINCT partner_trial_id) FROM trials"),
        "trials_with_chictr_id": one("SELECT COUNT(*) FROM trials WHERE chictr_registration_number IS NOT NULL"),
        "organizations": one("SELECT COUNT(*) FROM organizations"),
        "recruiting_trials": one("SELECT COUNT(*) FROM trials WHERE LOWER(recruitment_status)='recruiting' OR recruitment_status='招募中'"),
        "china_country_trials": one("SELECT COUNT(*) FROM trials WHERE countries LIKE '%China%' OR countries LIKE '%中国%'"),
        "trials_with_criteria": one("SELECT COUNT(DISTINCT trial_id) FROM eligibility_criteria"),
        "criteria": one("SELECT COUNT(*) FROM eligibility_criteria"),
        "criterion_types": dict(connection.execute("SELECT criterion_type, COUNT(*) FROM eligibility_criteria GROUP BY criterion_type").fetchall()),
        "structured_fields": dict(connection.execute("SELECT structured_field, COUNT(*) FROM eligibility_criteria WHERE structured_field IS NOT NULL GROUP BY structured_field").fetchall()),
        "source_registries": Counter(row["primary_registry"] for row in rows),
        "registration_date_min": dates[0].date().isoformat() if dates else None,
        "registration_date_max": dates[-1].date().isoformat() if dates else None,
    }
    if args.snapshot:
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
        report["source_snapshot"] = {
            "database_as_of": snapshot.get("metadata", {}).get("database_as_of"),
            "selection": snapshot.get("selection"),
            "detail_count": len(snapshot.get("details") or []),
            "failure_count": len(snapshot.get("failures") or {}),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2, default=dict))


if __name__ == "__main__":
    main()
