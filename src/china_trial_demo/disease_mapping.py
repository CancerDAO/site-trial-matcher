from __future__ import annotations

import json
from typing import Any

from .disease_ontology import DiseaseOntology
from .model_api import call_minimax_prompt
from .normalization import DiseaseMappingError, clean_text


MAPPING_FIELDS = (
    "cancer_type", "primary_disease", "histology", "disease_stage", "stage",
)


def _mapping_patient(patient: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "case_id": f"case-{index:04d}",
        "facts": {
            key: patient[key]
            for key in MAPPING_FIELDS
            if patient.get(key) not in (None, "", [])
        },
    }


def _prompt(cases: list[dict[str, Any]], ontology: DiseaseOntology) -> str:
    concepts = [
        {
            "id": item.concept_id,
            "label": item.label,
            "parent_id": item.parent_id,
            "aliases": list(item.aliases),
        }
        for item in ontology.concepts
    ]
    contract = {
        "mappings": [{
            "case_id": "case-0001",
            "status": "mapped | unmapped | ambiguous",
            "concept_id": "本体concept id或null",
            "confidence": "0到1",
            "evidence": {"patient_field": "字段名", "patient_text": "该字段中的逐字连续片段"},
            "candidate_ids": ["仅ambiguous时填写候选id"],
        }]
    }
    return (
        "你执行肿瘤疾病概念归一化，不判断试验资格。只能从给定本体选择concept_id，"
        "不得创造概念、不得根据治疗药物反推癌种、不得把组织学跨器官迁移。"
        "只有患者字段明确支持单一概念时使用mapped；多原发、来源冲突或多个互不兼容概念使用ambiguous；"
        "本体没有对应概念或证据不足使用unmapped。每个case必须恰好返回一次。"
        "mapped必须提供患者原字段中的逐字连续证据和不低于0.80的置信度。只返回JSON。\n\n"
        f"本体版本：{ontology.ontology_version}\n"
        f"本体：{json.dumps(concepts, ensure_ascii=False, separators=(',', ':'))}\n"
        f"患者：{json.dumps(cases, ensure_ascii=False, separators=(',', ':'))}\n"
        f"输出契约：{json.dumps(contract, ensure_ascii=False, separators=(',', ':'))}"
    )


def map_patients_with_model(
    patients: list[dict[str, Any]], ontology: DiseaseOntology
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    cases = [_mapping_patient(patient, index) for index, patient in enumerate(patients, start=1)]
    payload, api_meta = call_minimax_prompt(
        _prompt(cases, ontology),
        system="你是受控肿瘤疾病编码器，只能返回JSON并允许弃权。",
        max_completion_tokens=max(1024, min(8192, 384 * len(cases))),
    )
    rows = payload.get("mappings")
    if not isinstance(rows, list):
        raise ValueError("疾病映射模型输出缺少mappings数组")
    expected = {item["case_id"] for item in cases}
    actual = [str(item.get("case_id")) for item in rows if isinstance(item, dict)]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ValueError("疾病映射模型未完整覆盖患者或包含重复case_id")

    by_case = {str(item["case_id"]): item for item in rows}
    output: list[dict[str, Any]] = []
    for index, patient in enumerate(patients, start=1):
        patient_id = clean_text(patient.get("patient_id")) or f"case-{index:04d}"
        row = by_case[f"case-{index:04d}"]
        status = str(row.get("status") or "")
        if status != "mapped":
            candidates = [
                ontology.by_id[item].label for item in row.get("candidate_ids") or []
                if item in ontology.by_id
            ]
            raise DiseaseMappingError(
                patient_id,
                "ambiguous_disease_mapping" if status == "ambiguous" else "unmapped_disease",
                candidates or [clean_text(patient.get("cancer_type"))],
            )
        concept_id = str(row.get("concept_id") or "")
        concept = ontology.by_id.get(concept_id)
        confidence = row.get("confidence")
        if concept is None:
            raise DiseaseMappingError(patient_id, "canonical_disease_not_in_catalog", [concept_id])
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or confidence < 0.8 or confidence > 1:
            raise DiseaseMappingError(patient_id, "low_confidence_model_disease_mapping", [concept.label])
        evidence = row.get("evidence")
        if not isinstance(evidence, dict):
            raise DiseaseMappingError(patient_id, "invalid_model_disease_evidence", [concept.label])
        field = str(evidence.get("patient_field") or "")
        text = clean_text(evidence.get("patient_text"))
        source = clean_text(patient.get(field)) if field in MAPPING_FIELDS else ""
        if not text or text not in source:
            raise DiseaseMappingError(patient_id, "invalid_model_disease_evidence", [concept.label])
        output.append({
            **patient,
            "canonical_disease_id": concept.concept_id,
            "canonical_cancer_type": concept.label,
            "disease_mapping_method": "model_constrained",
            "disease_mapping_confidence": float(confidence),
            "disease_mapping_evidence": text,
        })
    return output, api_meta
