from __future__ import annotations

import sqlite3
from typing import Any

from .disease_ontology import DiseaseOntology, as_ontology
from .normalization import normalized_text

CLOSED_RECRUITMENT_STATUSES = {"暂停招募", "停止招募", "已完成"}
BROAD_DISEASE_TERMS = {"癌", "癌症", "肿瘤", "恶性肿瘤", "实体瘤", "晚期实体瘤", "cancer", "tumor", "tumour", "neoplasm", "solid tumor", "solid tumour"}
BROAD_DISEASE_QUALIFIERS = {"晚期", "转移性", "复发", "难治", "不可切除", "advanced", "metastatic", "recurrent", "refractory", "unresectable"}


def _contains(text: str, term: str) -> bool:
    left, right = normalized_text(text), normalized_text(term)
    return bool(left and right and (left in right or right in left))


def is_chinese_trial(trial: dict[str, Any]) -> bool:
    registry = str(trial.get("chictr_registration_number") or "").strip().casefold()
    source_url = str(trial.get("source_url") or "").strip().casefold()
    countries = normalized_text(trial.get("countries"))
    return registry.startswith("chictr") or "chictr.org.cn" in source_url or "china" in countries or "中国" in countries


def _group_ids(
    text: str, alias_groups: DiseaseOntology | list[list[str]]
) -> set[str]:
    return as_ontology(alias_groups).match(text)


def _is_broad_disease(text: Any) -> bool:
    key = normalized_text(text)
    broad_keys = {normalized_text(term) for term in BROAD_DISEASE_TERMS}
    if key in broad_keys:
        return True
    qualifiers = {normalized_text(term) for term in BROAD_DISEASE_QUALIFIERS}
    generic = {normalized_text(term) for term in ("恶性肿瘤", "实体瘤", "solid tumor", "solid tumour", "malignancy")}
    return any(term and term in key for term in generic) and any(term and term in key for term in qualifiers)


def disease_assessment(
    patient: dict[str, Any], trial: dict[str, Any],
    alias_groups: DiseaseOntology | list[list[str]],
) -> dict[str, Any]:
    """Conservative check: unknown or broad wording never excludes a trial."""
    ontology = as_ontology(alias_groups)
    patient_groups = set(patient.get("disease_group_ids") or [])
    if not patient_groups:
        patient_groups = _group_ids(str(patient.get("cancer_type") or ""), alias_groups)
    trial_text = " ".join(str(trial.get(key) or "") for key in ("primary_disease", "disease_aliases", "title_zh", "title_en"))
    trial_groups = _group_ids(trial_text, alias_groups)
    broad = _is_broad_disease(trial_text)
    hits = [term for term in patient.get("disease_terms", []) if _contains(trial_text, term)]
    compatible = bool(patient_groups and trial_groups and ontology.compatible(patient_groups, trial_groups))
    hard_mismatch = bool(patient_groups and trial_groups and not compatible and not broad)
    state = "明确不相容" if hard_mismatch else ("疾病相符" if hits or compatible else "信息不足")
    return {"state": state, "hard_mismatch": hard_mismatch, "patient_group_ids": sorted(patient_groups),
            "patient_canonical_disease": patient.get("canonical_cancer_type"),
            "patient_disease_mapping": patient.get("disease_mapping"),
            "trial_group_ids": sorted(trial_groups), "matched_terms": hits, "trial_disease_text": trial_text}


def relevance_score(patient: dict[str, Any], trial: dict[str, Any], disease: dict[str, Any]) -> tuple[float, dict[str, list[str]]]:
    """Ranking only; zero-score trials remain in the potential pool unless excluded."""
    score = 0.0
    matched: dict[str, list[str]] = {}
    if disease["matched_terms"]:
        matched["disease"] = disease["matched_terms"]
        score += 8.0 + min(4.0, float(len(disease["matched_terms"])))
    histology = str(patient.get("histology") or "")
    if histology and _contains(" ".join(str(trial.get(k) or "") for k in ("histology", "title_zh", "brief_summary")), histology):
        score += 3.0; matched["histology"] = [histology]
    marker_text = " ".join(str(trial.get(k) or "") for k in ("biomarker_summary", "title_zh", "title_en", "brief_summary"))
    marker_hits = [term for term in patient.get("biomarkers", []) if _contains(marker_text, term)]
    if marker_hits:
        score += min(6.0, 2.0 * len(marker_hits)); matched["biomarker"] = marker_hits
    stage = str(patient.get("disease_stage") or "")
    if stage and _contains(" ".join(str(trial.get(k) or "") for k in ("disease_stage", "title_zh", "brief_summary")), stage):
        score += 1.0; matched["stage"] = [stage]
    return score, matched


def load_trial_universe(connection: sqlite3.Connection, patient: dict[str, Any], *, alias_groups: DiseaseOntology | list[list[str]],
                        limit: int | None = None, include_inactive_partners: bool = True) -> list[dict[str, Any]]:
    rows = connection.execute("""
        SELECT t.*, o.organization_code, o.organization_name_zh, o.partner_status
        FROM trials t JOIN organizations o ON o.id=t.organization_id
        WHERE t.record_status='有效' ORDER BY t.id
    """).fetchall()
    results: list[dict[str, Any]] = []
    for row in rows:
        trial = dict(row)
        if not is_chinese_trial(trial):
            continue
        if not include_inactive_partners and trial["partner_status"] not in {"合作", "合作中", "demo_partner"}:
            continue
        disease = disease_assessment(patient, trial, alias_groups)
        score, matched = relevance_score(patient, trial, disease)
        trial.update(relevance_score=score, matched_dimensions=matched, disease_assessment=disease)
        results.append(trial)
    results.sort(key=lambda item: (-item["relevance_score"], item["id"]))
    return results[:limit] if limit and limit > 0 else results
