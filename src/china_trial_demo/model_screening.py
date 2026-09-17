from __future__ import annotations

import hashlib
import json
import os
import time
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path
from typing import Any

from .database import connect
from .model_api import call_minimax

PLACEHOLDER_PREFIX = "ChiCTR详情或"
VALID_DISPOSITIONS = {"exclude", "retain_for_review"}
VALID_BASES = {"scope_mismatch", "inclusion_failed", "exclusion_triggered"}
EXPECTED_SOURCE = {"scope_mismatch": "trial_scope", "inclusion_failed": "inclusion", "exclusion_triggered": "exclusion"}
SCOPE_PATIENT_FIELDS = {"cancer_type", "primary_disease", "histology", "disease_stage", "stage", "age", "sex", "mutations", "biomarkers", "biomarkers_known"}
SUPPORTIVE_CARE_TERMS = {
    "supportive care", "palliative care", "quality of life", "pain", "analges", "mucositis", "thrombocytopenia",
    "cachexia", "nutrition", "rehabilitation", "anxiety", "distress", "fatigue", "nausea", "sleep",
    "支持治疗", "姑息治疗", "生活质量", "疼痛", "镇痛", "黏膜炎", "血小板减少", "恶病质", "营养", "康复", "焦虑", "疲乏", "恶心", "睡眠",
}


class ModelRunCancelled(RuntimeError):
    pass


def safe_patient_job_key(patient_id: str, patient_index: int) -> str:
    digest = hashlib.sha256(patient_id.encode("utf-8")).hexdigest()[:12]
    return f"p{patient_index:04d}-{digest}"


MODEL_PATIENT_FIELDS = {
    "age", "sex", "cancer_type", "primary_disease", "histology", "disease_stage", "stage",
    "canonical_disease_id", "canonical_cancer_type", "disease_ontology_version",
    "disease_mapping_method", "disease_mapping_confidence", "disease_mapping_evidence",
    "mutations", "biomarkers", "biomarkers_known", "ecog", "prior_therapies",
    "treatment_lines_completed", "treatment_history", "current_therapy_ongoing",
    "comorbidities", "current_medications", "pregnant", "organ_function", "key_lab_trends",
    "brain_metastases", "viral_serology", "measurable_disease",
}


def _patient_map(path: str | Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            patient = json.loads(line); patient_id = str(patient["patient_id"])
            if patient_id in result: raise ValueError(f"患者编号重复：{patient_id}")
            result[patient_id] = patient
    return result


def _chunks(values: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    if size < 1: raise ValueError("批大小必须大于0")
    return [values[index:index+size] for index in range(0, len(values), size)]


def _model_patient(patient: dict[str, Any]) -> dict[str, Any]:
    """Allowlist clinical facts so direct identifiers never enter model jobs."""
    return {key: value for key, value in patient.items() if key in MODEL_PATIENT_FIELDS and value not in (None, "", [])}


def _is_bound_trial_text(text: str, sources: set[str]) -> bool:
    """Accept an exact source value or a verbatim continuous fragment, never a paraphrase."""
    if not text: return False
    if any(text == source for source in sources if source): return True
    compact = "".join(text.split())
    minimum = 12 if compact.isascii() else 5
    return len(compact) >= minimum and any(text in source for source in sources if source)


def _looks_supportive_care(trial: dict[str, Any]) -> bool:
    text = " ".join(str(trial.get(key) or "") for key in ("title", "title_en", "primary_disease", "disease_aliases")).casefold()
    return any(term.casefold() in text for term in SUPPORTIVE_CARE_TERMS)


def _coalesce_source_fragments(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Rebuild registry criteria wrapped at display width without inventing wording."""
    output: list[dict[str, Any]] = []
    for source in rows:
        row = dict(source); text = str(row.get("criterion_text_zh") or "").strip()
        if not text:
            continue
        if output:
            previous = output[-1]; previous_text = str(previous.get("criterion_text_zh") or "").rstrip()
            last_fragment = str(previous.get("_last_fragment_text") or previous_text)
            same_section = (previous.get("criterion_type"), previous.get("cohort_id")) == (row.get("criterion_type"), row.get("cohort_id"))
            wrapped = (len(last_fragment) >= 68 and not last_fragment.endswith((".", ";", ":", "。", "；", "：", "?", "!")))
            continuation = bool(text[:1].islower())
            if same_section and (wrapped or continuation):
                previous["criterion_text_zh"] = f"{previous_text} {text}"
                previous["criterion_id"] = f"{previous['criterion_id']}..{row['criterion_id']}"
                previous["_last_fragment_text"] = text
                continue
        row["_last_fragment_text"] = text
        output.append(row)
    return output


def _trial_payload(connection: Any, trial_id: int) -> tuple[str, dict[str, Any]]:
    trial = dict(connection.execute("SELECT * FROM trials WHERE id=?", (trial_id,)).fetchone())
    criteria = _coalesce_source_fragments([dict(row) for row in connection.execute("SELECT * FROM eligibility_criteria WHERE trial_id=? AND record_status='有效' ORDER BY criterion_type,criterion_order", (trial_id,))])
    cohorts = [dict(row) for row in connection.execute("SELECT cohort_id,cohort_name,cohort_status,disease_scope,biomarker_scope,intervention_name FROM trial_cohorts WHERE trial_id=? AND record_status='有效' ORDER BY id", (trial_id,))]
    raw = json.loads(trial.get("raw_json") or "{}")
    detail_available = bool(raw.get("detail_available", True))
    meaningful = [item for item in criteria if not str(item.get("criterion_text_zh") or "").startswith(PLACEHOLDER_PREFIX)]
    mode = "eligibility" if detail_available and meaningful else "scope"
    payload = {"trial_id": trial.get("chictr_registration_number") or trial.get("partner_trial_id") or f"本地试验-{trial_id}", "title": trial["title_zh"],
        "title_en": trial.get("title_en"), "primary_disease": trial.get("primary_disease"), "disease_aliases": trial.get("disease_aliases"),
        "histology": trial.get("histology"), "disease_stage": trial.get("disease_stage"), "biomarker_summary": trial.get("biomarker_summary"),
        "recruitment_status": trial.get("recruitment_status"), "cohorts": cohorts, "detail_available": detail_available}
    if mode == "eligibility":
        payload["inclusion"] = [{"criterion_id": item["criterion_id"], "cohort_id": item["cohort_id"], "text": item["criterion_text_zh"],
                                 "source_incomplete": str(item["criterion_text_zh"]).endswith("...")} for item in meaningful if item["criterion_type"] == "inclusion"]
        payload["exclusion"] = [{"criterion_id": item["criterion_id"], "cohort_id": item["cohort_id"], "text": item["criterion_text_zh"],
                                 "source_incomplete": str(item["criterion_text_zh"]).endswith("...")} for item in meaningful if item["criterion_type"] == "exclusion"]
    return mode, payload


def prepare_model_jobs(db_path: str | Path, patients_path: str | Path, deterministic_results_path: str | Path,
                       run_dir: str | Path, skill_path: str | Path, *, scope_batch_size: int = 8,
                       eligibility_batch_size: int = 2) -> dict[str, Any]:
    patients = _patient_map(patients_path); deterministic = json.loads(Path(deterministic_results_path).read_text(encoding="utf-8"))
    root = Path(run_dir); jobs_dir = root / "jobs"; results_dir = root / "results"; jobs_dir.mkdir(parents=True, exist_ok=True); results_dir.mkdir(parents=True, exist_ok=True)
    manifests: list[dict[str, Any]] = []
    with connect(db_path, readonly=True) as connection:
        for patient_index, patient_result in enumerate(deterministic["results"], start=1):
            patient_id = str(patient_result["patient_id"]); patient = patients[patient_id]
            disease = patient_result.get("patient_disease") or {}
            patient = {
                **patient,
                "canonical_disease_id": disease.get("concept_id"),
                "canonical_cancer_type": disease.get("canonical"),
                "disease_ontology_version": disease.get("ontology_version"),
                "disease_mapping_method": disease.get("method"),
                "disease_mapping_confidence": disease.get("confidence"),
                "disease_mapping_evidence": disease.get("evidence"),
            }
            # Partnership is an output dimension only. Keep medical job ordering stable
            # when an organization changes between partner and non-partner status.
            items = [*patient_result["potential_trials"]["non_partner"], *patient_result["potential_trials"]["partner"]]
            grouped: dict[str, list[dict[str, Any]]] = {"scope": [], "eligibility": []}
            for item in items:
                mode, payload = _trial_payload(connection, int(item["trial_id"])); grouped[mode].append(payload)
            for mode, trials in grouped.items():
                batch_size = scope_batch_size if mode == "scope" else eligibility_batch_size
                for number, batch in enumerate(_chunks(trials, batch_size), start=1):
                    # Patient identifiers are untrusted web input. Keep them in
                    # the payload and out of filesystem paths.
                    patient_key = safe_patient_job_key(patient_id, patient_index)
                    job_id = f"{patient_key}-{mode}-{number:04d}"
                    job_path = jobs_dir / f"{job_id}.json"; result_path = results_dir / f"{job_id}.json"
                    job = {"job_id": job_id, "stage": "exclusion_gater", "mode": mode, "patient_id": patient_id,
                        "patient": _model_patient(patient), "trials": batch, "expected_trial_ids": [trial["trial_id"] for trial in batch],
                        "skill_path": str(Path(skill_path).resolve())}
                    job_path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
                    manifests.append({"job_id": job_id, "patient_id": patient_id, "mode": mode, "job_path": str(job_path.resolve()),
                        "result_path": str(result_path.resolve()), "trial_count": len(batch), "expected_trial_ids": job["expected_trial_ids"]})
    mode_counts = Counter(item["mode"] for item in manifests)
    patient_counts = Counter(item["patient_id"] for item in manifests)
    manifest = {"schema_version": "china-trial-model-jobs-v1", "db_path": str(Path(db_path).resolve()),
        "patients_path": str(Path(patients_path).resolve()), "deterministic_results_path": str(Path(deterministic_results_path).resolve()),
        "skill_path": str(Path(skill_path).resolve()), "job_count": len(manifests), "trial_assignments": sum(item["trial_count"] for item in manifests),
        "job_counts_by_mode": dict(mode_counts), "job_counts_by_patient": dict(patient_counts), "jobs": manifests}
    (root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return {key: manifest[key] for key in ("schema_version", "job_count", "trial_assignments", "job_counts_by_mode", "job_counts_by_patient")}


def validate_model_output(job: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("analyzed_trials")
    if not isinstance(rows, list): raise ValueError("模型输出缺少analyzed_trials数组")
    expected = list(job["expected_trial_ids"]); actual = [str(row.get("trial_id")) for row in rows if isinstance(row, dict)]
    if len(actual) != len(set(actual)) or set(actual) != set(expected): raise ValueError("模型输出试验ID覆盖不完整或有重复")
    trials = {trial["trial_id"]: trial for trial in job["trials"]}; patient = job["patient"]; normalized: list[dict[str, Any]] = []
    for row in rows:
        trial = trials[str(row["trial_id"])]; disposition = row.get("disposition"); basis = row.get("exclusion_basis")
        if disposition not in VALID_DISPOSITIONS: raise ValueError(f"{row['trial_id']}结论值无效")
        if disposition == "retain_for_review" and basis is not None: raise ValueError(f"{row['trial_id']}保留结论不得有排除类型")
        if disposition == "exclude" and basis not in VALID_BASES: raise ValueError(f"{row['trial_id']}排除类型无效")
        allowed = {"trial_scope": {str(trial.get("title") or ""), str(trial.get("title_en") or ""), str(trial.get("primary_disease") or ""), str(trial.get("disease_aliases") or "")},
                   "inclusion": {str(item.get("text") or "") for item in trial.get("inclusion") or [] if isinstance(item, dict)},
                   "exclusion": {str(item.get("text") or "") for item in trial.get("exclusion") or [] if isinstance(item, dict)}}
        evidence = row.get("evidence"); restored: dict[str, Any] | None = None
        if evidence is not None:
            if not isinstance(evidence, dict): raise ValueError(f"{row['trial_id']}证据必须是对象或null")
            item = evidence
            source_kind = item.get("source_kind"); text = str(item.get("trial_text") or ""); field = str(item.get("patient_field") or "")
            if source_kind not in allowed or not _is_bound_trial_text(text, allowed[source_kind]):
                raise ValueError(f"{row['trial_id']}包含未绑定的试验证据")
            if field not in patient or patient[field] in (None, "", []): raise ValueError(f"{row['trial_id']}包含不存在或为空的患者字段")
            restored = {**item, "patient_value": patient[field]}
        if disposition == "exclude":
            if not restored or restored.get("certainty") != "clear" or restored.get("source_kind") != EXPECTED_SOURCE[basis]:
                raise ValueError(f"{row['trial_id']}排除结论没有匹配类型的明确证据")
            if basis == "scope_mismatch" and restored.get("patient_field") not in SCOPE_PATIENT_FIELDS:
                raise ValueError(f"{row['trial_id']}范围不相容使用了不允许的患者字段")
            if basis == "scope_mismatch" and _looks_supportive_care(trial):
                raise ValueError(f"{row['trial_id']}疑似肿瘤支持/症状管理试验，不得仅凭范围文字排除")
        elif restored is not None:
            raise ValueError(f"{row['trial_id']}保留结论不得携带排除证据")
        confidence = row.get("confidence")
        if disposition == "retain_for_review" and confidence is None: confidence = 0.0
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1: raise ValueError(f"{row['trial_id']}置信度无效")
        if disposition == "exclude" and confidence < 0.8: raise ValueError(f"{row['trial_id']}排除置信度低于0.80，应保留复核")
        normalized.append({"trial_id": row["trial_id"], "disposition": disposition, "exclusion_basis": basis, "confidence": confidence,
            "evidence": restored, "review_note": str(row.get("review_note") or "")})
    return {"analyzed_trials": normalized}


def _execute_one(item: dict[str, Any], should_cancel=lambda: False) -> dict[str, Any]:
    if should_cancel():
        raise ModelRunCancelled("model run cancelled")
    job = json.loads(Path(item["job_path"]).read_text(encoding="utf-8")); output = Path(item["result_path"])
    if output.is_file():
        try:
            payload = json.loads(output.read_text(encoding="utf-8")); validate_model_output(job, payload)
            return {"job_id": item["job_id"], "status": "resumed", "trial_count": item["trial_count"]}
        except (ValueError, json.JSONDecodeError):
            pass
    validation_retries = int(os.environ.get("MINIMAX_VALIDATION_RETRIES", "5")); feedback = ""; attempts: list[dict[str, Any]] = []
    for validation_attempt in range(validation_retries + 1):
        if should_cancel():
            raise ModelRunCancelled("model run cancelled")
        attempt_job = {**job, "validation_feedback": feedback or None}; payload, api_meta = call_minimax(attempt_job); attempts.append(api_meta)
        if should_cancel():
            raise ModelRunCancelled("model run cancelled")
        try:
            validated = validate_model_output(job, payload); break
        except ValueError as error:
            rejected = output.with_name(f"{output.stem}.rejected-{validation_attempt + 1}.json")
            rejected.write_text(json.dumps({"error": str(error), "payload": payload, "api_meta": api_meta}, ensure_ascii=False, indent=2), encoding="utf-8")
            feedback = f"上一次输出未通过校验：{error}。请修正；不确定的试验必须retain_for_review。"
    else:
        raise ValueError(f"{item['job_id']}连续{validation_retries + 1}次未通过模型输出校验：{feedback}")
    api_meta = {**attempts[-1], "validation_attempts": len(attempts), "all_attempts": attempts}
    result = {**validated, "api_meta": api_meta}; temporary = output.with_suffix(".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"); temporary.replace(output)
    return {"job_id": item["job_id"], "status": "completed", "trial_count": item["trial_count"], **api_meta}


def execute_model_jobs(run_dir: str | Path, *, workers: int = 8, should_cancel=lambda: False,
                       on_progress=None) -> dict[str, Any]:
    root = Path(run_dir); manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8")); started = time.perf_counter(); results: list[dict[str, Any]] = []
    jobs = iter(manifest["jobs"]); pending = {}; cancelled = False
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        def submit_next() -> bool:
            try: item = next(jobs)
            except StopIteration: return False
            pending[executor.submit(_execute_one, item, should_cancel)] = item
            return True
        for _ in range(max(1, workers)):
            if not submit_next(): break
        while pending:
            if should_cancel(): cancelled = True
            done, _ = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)
            for future in done:
                item = pending.pop(future)
                try:
                    results.append(future.result())
                except ModelRunCancelled:
                    cancelled = True
                except Exception as error:
                    results.append({"job_id": item["job_id"], "status": "failed", "trial_count": item["trial_count"], "error": str(error)})
                if on_progress:
                    on_progress(len(results), manifest["job_count"])
                if not cancelled:
                    submit_next()
        if cancelled:
            raise ModelRunCancelled("model run cancelled")
    summary = {"job_count": len(results), "trial_assignments": sum(item["trial_count"] for item in results),
        "failed_job_count": sum(item["status"] == "failed" for item in results),
        "elapsed_ms": round((time.perf_counter()-started)*1000, 3), "results": sorted(results, key=lambda item: item["job_id"])}
    (root / "execution-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def merge_model_results(run_dir: str | Path, output_path: str | Path) -> dict[str, Any]:
    root = Path(run_dir); manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8")); deterministic = json.loads(Path(manifest["deterministic_results_path"]).read_text(encoding="utf-8"))
    model_rows: dict[tuple[str, str], dict[str, Any]] = {}
    fallback_jobs: list[dict[str, str]] = []

    def retain_fallback(patient_id: str, trial_ids: list[str], reason: Exception) -> None:
        message = f"模型批次未完成或未通过校验，按保守原则保留人工复核：{reason}"
        fallback_jobs.append({"patient_id": patient_id, "error": str(reason)})
        for trial_id in trial_ids:
            key = (patient_id, str(trial_id))
            if key in model_rows:
                raise ValueError("模型任务包含重复的患者-试验组合")
            model_rows[key] = {"trial_id": str(trial_id), "disposition": "retain_for_review",
                "exclusion_basis": None, "confidence": 0.0, "evidence": None, "review_note": message}
    if manifest.get("db_path") and manifest.get("patients_path"):
        patients = _patient_map(manifest["patients_path"])
        trial_refs: dict[tuple[str, str], int] = {}
        expected_keys: set[tuple[str, str]] = set()
        for patient_result in deterministic["results"]:
            patient_id = str(patient_result["patient_id"])
            for group in patient_result["potential_trials"].values():
                for trial in group:
                    registry_id = str(trial.get("registry_id") or trial.get("chictr_registration_number") or f"本地试验-{trial['trial_id']}")
                    key = (patient_id, registry_id); expected_keys.add(key); trial_refs[key] = int(trial["trial_id"])
        with connect(manifest["db_path"], readonly=True) as connection:
            for item in manifest["jobs"]:
                job = json.loads(Path(item["job_path"]).read_text(encoding="utf-8"))
                try:
                    payload = json.loads(Path(item["result_path"]).read_text(encoding="utf-8"))
                    validated_batch = validate_model_output(job, payload)
                    batch_rows: dict[tuple[str, str], dict[str, Any]] = {}
                    for raw_row in validated_batch["analyzed_trials"]:
                        key = (item["patient_id"], str(raw_row.get("trial_id")))
                        if key in model_rows or key in batch_rows or key not in trial_refs:
                            raise ValueError("模型结果包含重复或当前候选之外的试验ID")
                        _, trial_payload = _trial_payload(connection, trial_refs[key])
                        single_job = {"expected_trial_ids": [key[1]], "patient": _model_patient(patients[key[0]]), "trials": [trial_payload]}
                        batch_rows[key] = validate_model_output(single_job, {"analyzed_trials": [raw_row]})["analyzed_trials"][0]
                    model_rows.update(batch_rows)
                except (OSError, ValueError, json.JSONDecodeError) as error:
                    retain_fallback(item["patient_id"], list(item["expected_trial_ids"]), error)
        if set(model_rows) != expected_keys: raise ValueError("模型结果未完整覆盖当前潜在试验")
    else:
        for item in manifest["jobs"]:
            job = json.loads(Path(item["job_path"]).read_text(encoding="utf-8"))
            try:
                payload = json.loads(Path(item["result_path"]).read_text(encoding="utf-8")); validated = validate_model_output(job, payload)
                for row in validated["analyzed_trials"]: model_rows[(item["patient_id"], row["trial_id"])] = row
            except (OSError, ValueError, json.JSONDecodeError) as error:
                retain_fallback(item["patient_id"], list(item.get("expected_trial_ids") or job["expected_trial_ids"]), error)
    for patient_result in deterministic["results"]:
        patient_id = str(patient_result["patient_id"])
        for partner_key in ("partner", "non_partner"):
            retained: list[dict[str, Any]] = []
            for trial in patient_result["potential_trials"][partner_key]:
                model_id = trial.get("registry_id") or trial.get("chictr_registration_number") or f"本地试验-{trial['trial_id']}"; model = model_rows[(patient_id, model_id)]
                if model["disposition"] == "exclude":
                    trial["disposition"] = "model_supported_exclusion"; trial["exclusion_authority"] = "language_model_with_evidence_binding"
                    trial["model_screening"] = model; patient_result["excluded_trials"][partner_key].append(trial)
                else:
                    trial["model_screening"] = model; retained.append(trial)
            patient_result["potential_trials"][partner_key] = retained
        summary = patient_result["summary"]
        for partner_key, prefix in (("partner", "partner"), ("non_partner", "non_partner")):
            summary[f"{prefix}_potential_count"] = len(patient_result["potential_trials"][partner_key])
            summary[f"{prefix}_excluded_count"] = len(patient_result["excluded_trials"][partner_key])
        summary["potential_count"] = summary["partner_potential_count"] + summary["non_partner_potential_count"]
        summary["excluded_count"] = summary["partner_excluded_count"] + summary["non_partner_excluded_count"]
    deterministic["schema_version"] = "china-trial-exclusion-first-results-v3-model-screened"
    deterministic["model_run"] = {"run_dir": str(root.resolve()), "job_count": manifest["job_count"], "trial_assignments": manifest["trial_assignments"],
        "fallback_job_count": len(fallback_jobs), "fallback_jobs": fallback_jobs}
    output = Path(output_path); output.parent.mkdir(parents=True, exist_ok=True); output.write_text(json.dumps(deterministic, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"output": str(output.resolve()), "patient_count": deterministic["patient_count"], "model_assignments": manifest["trial_assignments"]}
