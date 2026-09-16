from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .normalization import clean_text, list_value, normalized_text


@dataclass(frozen=True)
class PredicateResult:
    state: str
    patient_value: Any
    trial_value: str
    reason: str


def _patient_value(patient: dict[str, Any], field: str) -> Any:
    mapping = {"age_years": "age", "maximum_ecog": "ecog", "stage": "disease_stage", "biomarker": "biomarkers", "mutation": "biomarkers", "pregnancy": "pregnant"}
    return patient.get(mapping.get(field, field))


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try: return float(value)
    except (TypeError, ValueError): return None


def _targets(value: str) -> list[str]:
    return [part.strip() for part in str(value or "").split("|") if part.strip()]


def _comparison_key(field: str, value: Any) -> str:
    """Normalize small controlled vocabularies before deterministic comparison."""
    key = normalized_text(value)
    if field == "sex":
        if key in {"男", "男性", "male", "man", "m"}:
            return "male"
        if key in {"女", "女性", "female", "woman", "f"}:
            return "female"
    return key


def evaluate_predicate(patient: dict[str, Any], criterion: dict[str, Any]) -> PredicateResult:
    field, operator = clean_text(criterion.get("structured_field")), clean_text(criterion.get("operator"))
    trial_value = clean_text(criterion.get("structured_value"))
    if not field or not operator:
        return PredicateResult("unknown", None, trial_value, "仅有自由文本，不能确定性判断")
    value = _patient_value(patient, field)
    if value in (None, "", []):
        return PredicateResult("unknown", value, trial_value, f"患者字段 {field} 缺失")
    if operator in {"gte", "lte", "between"}:
        patient_number = _number(value); target_numbers = [_number(item) for item in _targets(trial_value)]
        if patient_number is None or not target_numbers or any(item is None for item in target_numbers):
            return PredicateResult("unknown", value, trial_value, "数值或单位无法可靠标准化")
        if operator == "gte": matched = patient_number >= target_numbers[0]
        elif operator == "lte": matched = patient_number <= target_numbers[0]
        elif len(target_numbers) == 2: matched = target_numbers[0] <= patient_number <= target_numbers[1]
        else: return PredicateResult("unknown", value, trial_value, "介于条件必须提供最小值|最大值")
        return PredicateResult("true" if matched else "false", value, trial_value, f"{field} {operator} {trial_value}")
    if operator in {"exists", "not_exists"}:
        exists = value not in (None, "", []); matched = exists if operator == "exists" else not exists
        return PredicateResult("true" if matched else "false", value, trial_value, f"{field} {operator}")
    patient_keys = [_comparison_key(field, item) for item in (list_value(value) or [clean_text(value)]) if _comparison_key(field, item)]
    target_keys = [_comparison_key(field, item) for item in (_targets(trial_value) or [trial_value]) if _comparison_key(field, item)]
    if not patient_keys or not target_keys:
        return PredicateResult("unknown", value, trial_value, "文本比较值为空")
    equality = any(p == t for p in patient_keys for t in target_keys)
    containment = any(p in t or t in p for p in patient_keys for t in target_keys)
    if operator == "eq": matched = equality
    elif operator == "neq": matched = not equality
    elif operator in {"in", "contains"}: matched = containment
    elif operator in {"not_in", "not_contains"}: matched = not containment
    else: return PredicateResult("unknown", value, trial_value, f"不支持的运算符 {operator}")
    return PredicateResult("true" if matched else "false", value, trial_value, f"{field} {operator} {trial_value}")


def _trial_level_criteria(trial: dict[str, Any]) -> list[dict[str, Any]]:
    criteria: list[dict[str, Any]] = []
    def add(identifier: str, field: str, operator: str, value: Any, text: str) -> None:
        if value not in (None, ""):
            criteria.append({"criterion_type": "inclusion", "criterion_id": identifier, "criterion_category": field,
                "criterion_text_zh": text, "is_mandatory": 1, "structured_field": field, "operator": operator, "structured_value": str(value)})
    minimum, maximum = trial.get("minimum_age_years"), trial.get("maximum_age_years")
    if minimum not in (None, "") and maximum not in (None, ""):
        add("试验级-年龄", "age", "between", f"{minimum}|{maximum}", f"年龄 {minimum}-{maximum} 岁")
    else:
        add("试验级-最小年龄", "age", "gte", minimum, f"年龄不低于 {minimum} 岁")
        add("试验级-最大年龄", "age", "lte", maximum, f"年龄不高于 {maximum} 岁")
    add("试验级-ECOG", "ecog", "lte", trial.get("maximum_ecog"), f"ECOG 不高于 {trial.get('maximum_ecog')}")
    sex = clean_text(trial.get("sex"))
    if sex.casefold() not in {"", "不限", "所有", "全部", "男和女", "both", "all", "male and female"}: add("试验级-性别", "sex", "eq", sex, f"性别限 {sex}")
    return criteria


def evaluate_trial(patient: dict[str, Any], trial: dict[str, Any], criteria: list[dict[str, Any]]) -> dict[str, Any]:
    evaluations: list[dict[str, Any]] = []; exclusion_reasons: list[dict[str, Any]] = []; unknowns: list[dict[str, Any]] = []
    for criterion in [*_trial_level_criteria(trial), *criteria]:
        result = evaluate_predicate(patient, criterion); criterion_type = criterion["criterion_type"]; mandatory = bool(criterion.get("is_mandatory", 1))
        if criterion_type == "inclusion":
            disposition = {"true": "满足", "false": "不满足", "unknown": "待复核"}[result.state]; excludes = mandatory and result.state == "false"
        else:
            disposition = {"true": "触发", "false": "未触发", "unknown": "待复核"}[result.state]; excludes = mandatory and result.state == "true"
        item = {"criterion_id": criterion["criterion_id"], "criterion_type": criterion_type, "category": criterion["criterion_category"],
            "disposition": disposition, "mandatory": mandatory, "patient_field": criterion.get("structured_field"), "patient_value": result.patient_value,
            "trial_value": result.trial_value, "criterion_text": criterion["criterion_text_zh"], "reason": result.reason,
            "decision_basis": "明确结构化事实" if result.state != "unknown" else "信息不足"}
        evaluations.append(item)
        if excludes: exclusion_reasons.append(item)
        elif mandatory and result.state == "unknown": unknowns.append(item)
    # This stage can only prove an exclusion or the absence of a deterministic
    # exclusion. It must never imply that a patient is eligible for enrollment.
    disposition = "excluded" if exclusion_reasons else ("needs_review" if unknowns else "no_deterministic_exclusion")
    return {"disposition": disposition, "trial_id": trial["id"], "chictr_registration_number": trial.get("chictr_registration_number"),
        "title": trial["title_zh"], "exclusion_reasons": exclusion_reasons, "unknown_criteria": unknowns, "criteria_evaluations": evaluations}
