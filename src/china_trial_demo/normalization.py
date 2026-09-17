from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from .disease_ontology import DiseaseOntology, as_ontology, load_disease_ontology
from .normalization_text import clean_text, normalized_text


class DiseaseMappingError(ValueError):
    """Raised when a patient cannot be mapped to one controlled disease concept."""

    def __init__(self, patient_id: str, code: str, candidates: list[str] | None = None):
        self.patient_id = patient_id
        self.code = code
        self.candidates = candidates or []
        detail = f"{patient_id}: {code}"
        if self.candidates:
            detail += f" ({', '.join(self.candidates)})"
        super().__init__(detail)


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


def load_aliases(path: str | Path) -> DiseaseOntology:
    """Compatibility name retained for callers while the data moves to an ontology."""
    return load_disease_ontology(path)


def canonical_disease_concepts(alias_groups: DiseaseOntology | list[list[str]]) -> list[dict[str, Any]]:
    return as_ontology(alias_groups).public_concepts()


def resolve_patient_disease(
    patient: dict[str, Any], alias_groups: DiseaseOntology | list[list[str]]
) -> dict[str, Any]:
    """Resolve one auditable disease concept or fail before matching starts."""
    ontology = as_ontology(alias_groups)
    patient_id = clean_text(patient.get("patient_id")) or "unknown-patient"
    supplied_id = clean_text(patient.get("canonical_disease_id"))
    supplied = clean_text(patient.get("canonical_cancer_type"))
    if supplied_id or supplied:
        concept = ontology.by_id.get(supplied_id) if supplied_id else ontology.canonical(supplied)
        if concept is None:
            raise DiseaseMappingError(
                patient_id, "canonical_disease_not_in_catalog", [supplied_id or supplied]
            )
        confidence = patient.get("disease_mapping_confidence")
        if confidence is not None:
            try:
                confidence = float(confidence)
            except (TypeError, ValueError) as error:
                raise DiseaseMappingError(patient_id, "invalid_disease_mapping_confidence") from error
            if not 0 <= confidence <= 1:
                raise DiseaseMappingError(patient_id, "invalid_disease_mapping_confidence")
        method = clean_text(patient.get("disease_mapping_method")) or "provided_controlled_concept"
        if method == "model_constrained" and (confidence is None or confidence < 0.8):
            raise DiseaseMappingError(patient_id, "low_confidence_model_disease_mapping", [supplied])
        return {
            "group_ids": [concept.concept_id],
            "canonical_disease_id": concept.concept_id,
            "canonical_cancer_type": concept.label,
            "ontology_version": ontology.ontology_version,
            "method": method,
            "confidence": confidence,
            "evidence": clean_text(patient.get("disease_mapping_evidence")) or supplied,
            "source_field": "canonical_cancer_type",
        }

    sources = [
        ("cancer_type", patient.get("cancer_type")),
        ("primary_disease", patient.get("primary_disease")),
        ("histology", patient.get("histology")),
        ("disease_stage", patient.get("disease_stage") or patient.get("stage")),
    ]
    matches_by_source: list[tuple[str, Any, set[str]]] = []
    all_matches: set[str] = set()
    for source_field, value in sources:
        matches = ontology.match(value)
        if matches:
            matches_by_source.append((source_field, value, matches))
            all_matches.update(matches)
    if not all_matches:
        raise DiseaseMappingError(patient_id, "unmapped_disease", [clean_text(patient.get("cancer_type"))])

    group_id = max(all_matches, key=lambda concept_id: ontology.depth(concept_id))
    if not all(ontology.related(group_id, candidate) for candidate in all_matches):
        labels = [ontology.by_id[concept_id].label for concept_id in sorted(all_matches)]
        raise DiseaseMappingError(patient_id, "ambiguous_disease_mapping", labels)
    source_field, value, _ = next(
        item for item in matches_by_source if group_id in item[2]
    )
    return {
        "group_ids": [group_id],
        "canonical_disease_id": group_id,
        "canonical_cancer_type": ontology.by_id[group_id].label,
        "ontology_version": ontology.ontology_version,
        "method": "deterministic_alias",
        "confidence": 1.0,
        "evidence": clean_text(value),
        "source_field": source_field,
    }


def expand_terms(
    values: Iterable[str], alias_groups: DiseaseOntology | list[list[str]]
) -> list[str]:
    ontology = as_ontology(alias_groups)
    seeds = dedupe(values)
    expanded = list(seeds)
    concept_ids: set[str] = set()
    for value in seeds:
        concept_ids.update(ontology.match(value))
    expanded_concept_ids = set(concept_ids)
    for concept_id in concept_ids:
        expanded_concept_ids.update(ontology.ancestors(concept_id))
    for concept_id in expanded_concept_ids:
        expanded.extend(ontology.by_id[concept_id].aliases)
    return dedupe(expanded)


def normalize_patient(
    patient: dict[str, Any], alias_groups: DiseaseOntology | list[list[str]]
) -> dict[str, Any]:
    patient_id = clean_text(patient.get("patient_id"))
    cancer_type = clean_text(patient.get("cancer_type") or patient.get("primary_disease"))
    if not patient_id:
        raise ValueError("patient_id is required")
    if not cancer_type:
        raise ValueError(f"{patient_id}: cancer_type is required")
    disease_mapping = resolve_patient_disease(patient, alias_groups)
    normalized = dict(patient)
    normalized["patient_id"] = patient_id
    normalized["cancer_type"] = cancer_type
    normalized["histology"] = clean_text(patient.get("histology"))
    normalized["disease_stage"] = clean_text(
        patient.get("disease_stage") or patient.get("stage")
    )
    normalized["canonical_cancer_type"] = disease_mapping["canonical_cancer_type"]
    normalized["canonical_disease_id"] = disease_mapping["canonical_disease_id"]
    normalized["disease_ontology_version"] = disease_mapping["ontology_version"]
    normalized["disease_group_ids"] = disease_mapping["group_ids"]
    normalized["disease_mapping"] = {
        key: disease_mapping[key]
        for key in ("method", "confidence", "evidence", "source_field")
    }
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
    normalized["disease_terms"] = expand_terms(
        [cancer_type, disease_mapping["canonical_cancer_type"]], alias_groups
    )
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
