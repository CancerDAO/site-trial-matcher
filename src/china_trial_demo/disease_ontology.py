from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .normalization_text import normalized_text


def _alias_in_text(value: Any, alias: str) -> bool:
    raw_text = str(value or "").casefold()
    raw_alias = str(alias or "").strip().casefold()
    key = normalized_text(value)
    alias_key = normalized_text(alias)
    if not alias_key:
        return False
    if raw_alias == "sclc":
        return re.search(r"(?<![a-z0-9])sclc(?![a-z0-9])", raw_text) is not None
    if raw_alias == "small cell lung cancer":
        return re.search(r"(?<!non[- ])\bsmall[ -]cell[ -]lung[ -]cancer\b", raw_text) is not None
    if alias_key == "小细胞肺癌":
        return re.search(r"(?<!非)小细胞肺癌", key) is not None
    if raw_alias.isascii() and re.fullmatch(r"[a-z0-9]+", raw_alias):
        return re.search(rf"(?<![a-z0-9]){re.escape(raw_alias)}(?![a-z0-9])", raw_text) is not None
    return alias_key in key


@dataclass(frozen=True)
class DiseaseConcept:
    concept_id: str
    label: str
    aliases: tuple[str, ...]
    parent_id: str | None = None
    broad_scope: bool = False


class DiseaseOntology:
    def __init__(self, *, schema_version: str, ontology_version: str, concepts: Iterable[DiseaseConcept]):
        self.schema_version = schema_version
        self.ontology_version = ontology_version
        self.concepts = tuple(concepts)
        self.by_id = {item.concept_id: item for item in self.concepts}
        if len(self.by_id) != len(self.concepts):
            raise ValueError("疾病本体包含重复concept_id")
        for item in self.concepts:
            if item.parent_id and item.parent_id not in self.by_id:
                raise ValueError(f"疾病本体父概念不存在：{item.concept_id} -> {item.parent_id}")
            if not item.aliases:
                raise ValueError(f"疾病本体概念没有别名：{item.concept_id}")
        for item in self.concepts:
            self.ancestors(item.concept_id)

    def ancestors(self, concept_id: str) -> set[str]:
        result: set[str] = set()
        current = self.by_id[concept_id]
        while current.parent_id:
            if current.parent_id in result:
                raise ValueError(f"疾病本体存在父子循环：{concept_id}")
            result.add(current.parent_id)
            current = self.by_id[current.parent_id]
        return result

    def depth(self, concept_id: str) -> int:
        return len(self.ancestors(concept_id))

    def related(self, left: str, right: str) -> bool:
        return left == right or left in self.ancestors(right) or right in self.ancestors(left)

    def compatible(self, left: Iterable[str], right: Iterable[str]) -> bool:
        return any(self.related(a, b) for a in left for b in right)

    def most_specific(self, concept_ids: Iterable[str]) -> set[str]:
        values = set(concept_ids)
        return {
            concept_id for concept_id in values
            if not any(concept_id in self.ancestors(other) for other in values if other != concept_id)
        }

    def match(self, value: Any) -> set[str]:
        key = normalized_text(value)
        if not key:
            return set()
        exact = {
            item.concept_id for item in self.concepts
            if key in {normalized_text(alias) for alias in item.aliases}
        }
        if exact:
            return self.most_specific(exact)
        contained = {
            item.concept_id for item in self.concepts
            if any(_alias_in_text(value, alias) for alias in item.aliases)
        }
        return self.most_specific(contained)

    def canonical(self, value: Any) -> DiseaseConcept | None:
        key = normalized_text(value)
        matches = [item for item in self.concepts if key == normalized_text(item.label)]
        return matches[0] if len(matches) == 1 else None

    def public_concepts(self) -> list[dict[str, Any]]:
        return [
            {
                "id": item.concept_id,
                "label": item.label,
                "parent_id": item.parent_id,
                "aliases": list(item.aliases),
                "broad_scope": item.broad_scope,
            }
            for item in self.concepts
        ]


def _legacy_ontology(groups: list[list[str]]) -> DiseaseOntology:
    return DiseaseOntology(
        schema_version="legacy-alias-groups-v1",
        ontology_version="legacy",
        concepts=[
            DiseaseConcept(f"legacy-{index:03d}", str(group[0]), tuple(str(value) for value in group))
            for index, group in enumerate(groups)
            if group
        ],
    )


def as_ontology(value: DiseaseOntology | list[list[str]]) -> DiseaseOntology:
    return value if isinstance(value, DiseaseOntology) else _legacy_ontology(value)


def load_disease_ontology(path: str | Path) -> DiseaseOntology:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "concepts" not in data:
        return _legacy_ontology(data.get("groups") or [])
    return DiseaseOntology(
        schema_version=str(data.get("schema_version") or ""),
        ontology_version=str(data.get("ontology_version") or ""),
        concepts=[
            DiseaseConcept(
                concept_id=str(item["id"]),
                label=str(item["label"]),
                aliases=tuple(dict.fromkeys([str(item["label"]), *map(str, item.get("aliases") or [])])),
                parent_id=str(item["parent_id"]) if item.get("parent_id") else None,
                broad_scope=bool(item.get("broad_scope", False)),
            )
            for item in data["concepts"]
        ],
    )
