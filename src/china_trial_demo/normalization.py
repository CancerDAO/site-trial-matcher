from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def normalized_text(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", clean_text(value).casefold())


def list_value(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, dict):
        return [f"{key} {item}".strip() for key, item in value.items()]
    if isinstance(value, (list, tuple, set)):
        return [clean_text(item) for item in value if clean_text(item)]
    text = clean_text(value)
    if not text:
        return []
    return [part.strip() for part in re.split(r"[|;,；，]", text) if part.strip()]


def dedupe(values: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        key = normalized_text(value)
        if key and key not in seen:
            seen.add(key)
            result.append(clean_text(value))
    return result


def load_aliases(path: str | Path) -> list[list[str]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [dedupe(group) for group in data["groups"]]


def expand_terms(values: Iterable[str], alias_groups: list[list[str]]) -> list[str]:
    seeds = dedupe(values)
    keys = {normalized_text(value) for value in seeds}
    expanded = list(seeds)
    exact_groups = [group for group in alias_groups if keys & {normalized_text(value) for value in group}]
    if exact_groups:
        for group in exact_groups:
            expanded.extend(group)
        return dedupe(expanded)
    for group in alias_groups:
        group_keys = {normalized_text(value) for value in group}
        if any(left in right or right in left for left in keys for right in group_keys):
            expanded.extend(group)
    return dedupe(expanded)


def normalize_patient(patient: dict[str, Any], alias_groups: list[list[str]]) -> dict[str, Any]:
    patient_id = clean_text(patient.get("patient_id"))
    cancer_type = clean_text(patient.get("cancer_type") or patient.get("primary_disease"))
    if not patient_id:
        raise ValueError("patient_id is required")
    if not cancer_type:
        raise ValueError(f"{patient_id}: cancer_type is required")
    normalized = dict(patient)
    normalized["patient_id"] = patient_id
    normalized["cancer_type"] = cancer_type
    normalized["histology"] = clean_text(patient.get("histology"))
    normalized["disease_stage"] = clean_text(
        patient.get("disease_stage") or patient.get("stage")
    )
    normalized["mutations"] = list_value(patient.get("mutations"))
    normalized["biomarkers"] = dedupe(
        list_value(patient.get("biomarkers"))
        + list_value(patient.get("biomarkers_known"))
        + normalized["mutations"]
    )
    normalized["prior_therapies"] = list_value(patient.get("prior_therapies"))
    normalized["comorbidities"] = list_value(patient.get("comorbidities"))
    normalized["current_medications"] = list_value(patient.get("current_medications"))
    # Histology is a secondary ranking signal. Treating generic words such as
    # "adenocarcinoma/腺癌" as a disease term creates cross-organ false recall.
    normalized["disease_terms"] = expand_terms([cancer_type], alias_groups)
    for key in ("age", "ecog", "treatment_lines_completed"):
        value = patient.get(key)
        if value in (None, ""):
            normalized[key] = None
        else:
            try:
                normalized[key] = float(value)
            except (TypeError, ValueError) as error:
                raise ValueError(f"{patient_id}: {key} must be numeric") from error
    return normalized
