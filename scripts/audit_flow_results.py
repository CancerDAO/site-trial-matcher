from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit exclusion-first result coverage and bucket invariants")
    parser.add_argument("result", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    payload = json.loads(args.result.read_text(encoding="utf-8"))
    patients = []
    all_ok = True
    for result in payload["results"]:
        potential = [*result["potential_trials"]["partner"], *result["potential_trials"]["non_partner"]]
        excluded = [*result["excluded_trials"]["partner"], *result["excluded_trials"]["non_partner"]]
        rows = [*potential, *excluded]
        ids = [item["trial_id"] for item in rows]
        reasons = Counter(reason.get("reason_code") or item.get("disposition") or "OTHER"
                          for item in excluded for reason in (item.get("exclusion_reasons") or [{}]))
        checks = {
            "universe_conserved": len(rows) == result["trial_universe_count"],
            "no_duplicate_trial_ids": len(ids) == len(set(ids)),
            "summary_counts_match": (len(potential) == result["summary"]["potential_count"]
                                     and len(excluded) == result["summary"]["excluded_count"]),
            "partnership_buckets_match": (
                len(result["potential_trials"]["partner"]) == result["summary"]["partner_potential_count"]
                and len(result["potential_trials"]["non_partner"]) == result["summary"]["non_partner_potential_count"]
                and len(result["excluded_trials"]["partner"]) == result["summary"]["partner_excluded_count"]
                and len(result["excluded_trials"]["non_partner"]) == result["summary"]["non_partner_excluded_count"]),
            "potential_has_no_exclusion_reason": all(not item.get("exclusion_reasons") for item in potential),
        }
        all_ok = all_ok and all(checks.values())
        patients.append({"patient_id": result["patient_id"], "universe": result["trial_universe_count"],
                         "potential": len(potential), "excluded": len(excluded),
                         "total_ms": result["timing_ms"]["total"], "exclusion_reason_counts": dict(reasons),
                         "checks": checks})
    report = {"source": str(args.result.resolve()), "patient_count": len(patients), "all_invariants_passed": all_ok,
              "batch_total_ms": payload.get("total_ms"), "average_patient_ms": payload.get("average_patient_ms"),
              "patients": patients}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
