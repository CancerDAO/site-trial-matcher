from __future__ import annotations

import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from .database import connect
from .eligibility import evaluate_trial
from .normalization import load_aliases, normalize_patient
from .retrieval import CLOSED_RECRUITMENT_STATUSES, load_trial_universe

PARTNER_STATUSES = {"合作", "合作中", "demo_partner"}


def _base_item(trial: dict[str, Any]) -> dict[str, Any]:
    registry_id = trial.get("chictr_registration_number") or trial.get("partner_trial_id")
    return {"trial_id": trial["id"], "registry_id": registry_id,
        "chictr_registration_number": trial.get("chictr_registration_number"), "title": trial["title_zh"],
        "organization_code": trial["organization_code"], "organization_name": trial["organization_name_zh"], "partner_status": trial["partner_status"],
        "recruitment_status": trial["recruitment_status"], "source_url": trial["source_url"], "relevance_score": trial["relevance_score"],
        "matched_dimensions": trial["matched_dimensions"], "disease_assessment": trial["disease_assessment"]}


def match_patient(db_path: str | Path, patient: dict[str, Any], *, aliases_path: str | Path,
                  candidate_limit: int | None = None) -> dict[str, Any]:
    started = time.perf_counter(); aliases = load_aliases(aliases_path); normalized = normalize_patient(patient, aliases)
    normalized_at = time.perf_counter()
    with connect(db_path, readonly=True) as connection:
        trials = load_trial_universe(connection, normalized, alias_groups=aliases, limit=candidate_limit)
        retrieved_at = time.perf_counter()
        criteria_by_trial: dict[int, list[dict[str, Any]]] = defaultdict(list)
        if trials:
            ids = [trial["id"] for trial in trials]; placeholders = ",".join("?" for _ in ids)
            for row in connection.execute(
                f"SELECT * FROM eligibility_criteria WHERE record_status='有效' AND trial_id IN ({placeholders}) ORDER BY trial_id,criterion_type,criterion_order", ids
            ):
                criteria_by_trial[row["trial_id"]].append(dict(row))
        potential: list[dict[str, Any]] = []; excluded: list[dict[str, Any]] = []
        for trial in trials:
            base = _base_item(trial); reasons: list[dict[str, Any]] = []
            if trial["recruitment_status"] in CLOSED_RECRUITMENT_STATUSES:
                reasons.append({"reason_code": "RECRUITMENT_CLOSED", "reason": f"当前招募状态为{trial['recruitment_status']}", "decision_basis": "试验明确状态"})
            if trial["disease_assessment"]["hard_mismatch"]:
                reasons.append({"reason_code": "DISEASE_MISMATCH", "reason": "患者疾病与试验疾病属于不同且明确的癌种组", "decision_basis": "双方明确疾病字段"})
            if reasons:
                excluded.append({**base, "disposition": "excluded", "exclusion_reasons": reasons, "unknown_criteria": [], "criteria_evaluations": []})
                continue
            evaluation = evaluate_trial(normalized, trial, criteria_by_trial[trial["id"]]); item = {**evaluation, **base}
            (excluded if evaluation["disposition"] == "excluded" else potential).append(item)
        evaluated_at = time.perf_counter()
    partner_potential_trials = [item for item in potential if item["partner_status"] in PARTNER_STATUSES]
    non_partner_potential_trials = [item for item in potential if item["partner_status"] not in PARTNER_STATUSES]
    partner_excluded_trials = [item for item in excluded if item["partner_status"] in PARTNER_STATUSES]
    non_partner_excluded_trials = [item for item in excluded if item["partner_status"] not in PARTNER_STATUSES]
    partner_potential = len(partner_potential_trials); partner_excluded = len(partner_excluded_trials)
    return {"patient_id": normalized["patient_id"], "trial_universe_count": len(trials),
        "potential_trials": {"partner": partner_potential_trials, "non_partner": non_partner_potential_trials},
        "excluded_trials": {"partner": partner_excluded_trials, "non_partner": non_partner_excluded_trials},
        "summary": {"potential_count": len(potential), "excluded_count": len(excluded), "partner_potential_count": partner_potential,
            "non_partner_potential_count": len(potential)-partner_potential, "partner_excluded_count": partner_excluded,
            "non_partner_excluded_count": len(excluded)-partner_excluded},
        "timing_ms": {"normalize": round((normalized_at-started)*1000,3), "load_universe": round((retrieved_at-normalized_at)*1000,3),
            "prescreen": round((evaluated_at-retrieved_at)*1000,3), "total": round((evaluated_at-started)*1000,3)}}


def match_batch(db_path: str | Path, patients_path: str | Path, output_path: str | Path, *, aliases_path: str | Path,
                candidate_limit: int | None = None) -> dict[str, Any]:
    patients = [json.loads(line) for line in Path(patients_path).read_text(encoding="utf-8").splitlines() if line.strip()]
    patient_ids = [str(patient.get("patient_id") or "") for patient in patients]
    if len(patient_ids) != len(set(patient_ids)):
        raise ValueError("患者输入包含重复patient_id")
    started = time.perf_counter(); results = [match_patient(db_path, patient, aliases_path=aliases_path, candidate_limit=candidate_limit) for patient in patients]
    total_ms = round((time.perf_counter()-started)*1000,3)
    payload = {"schema_version": "china-trial-exclusion-first-results-v2", "patient_count": len(results), "total_ms": total_ms,
        "average_patient_ms": round(total_ms/len(results),3) if results else 0, "results": results}
    output = Path(output_path); output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
