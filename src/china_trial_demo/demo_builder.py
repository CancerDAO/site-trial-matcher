from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .database import connect, initialize, replace_trial_search
from .normalization import clean_text

CANCER_MARKERS = ("癌", "肿瘤", "白血病", "淋巴瘤", "骨髓瘤", "肉瘤", "胶质瘤", "母细胞瘤", "carcinoma", "cancer", "tumor", "tumour", "neoplasm", "leukemia", "lymphoma", "sarcoma", "melanoma", "myeloma")
BIOMARKERS = ("EGFR", "KRAS", "NRAS", "BRAF", "HER2", "ALK", "ROS1", "RET", "MET", "BRCA", "PD-L1", "MSI", "MMR", "TMB", "NTRK")


def normalize_recruitment(value: Any) -> str:
    text = clean_text(value).casefold()
    if "尚未开始" in text or "not yet" in text: return "尚未开始"
    if "暂停" in text or "suspend" in text: return "暂停招募"
    if "正在" in text or "招募中" in text or "recruiting" in text: return "招募中"
    if "完成" in text or "completed" in text: return "已完成"
    if "停止" in text or "终止" in text or "terminated" in text: return "停止招募"
    return "未知"


def is_cancer_trial(detail: dict[str, Any]) -> bool:
    text = " ".join(clean_text(detail.get(key)) for key in ("disease", "disease_en", "title", "title_en", "objectives", "objectives_en")).casefold()
    return any(marker.casefold() in text for marker in CANCER_MARKERS)


def split_criteria(text: Any) -> list[str]:
    value = str(text or "").replace("\r", "\n")
    value = re.sub(r"(?m)^\s*(\d{1,2})[\.、]\s*", r"\1. ", value)
    parts = [re.sub(r"^\s*\d{1,2}[\.、]\s*", "", part).strip(" ;；\t") for part in value.split("\n")]
    return [part for part in parts if len(part) >= 2]


def category_for(text: str) -> str:
    upper = text.upper()
    if "ECOG" in upper or "KPS" in upper: return "ecog"
    if "年龄" in text or "岁" in text or " age " in f" {text.casefold()} ": return "age"
    if any(marker in upper for marker in BIOMARKERS): return "biomarker"
    if any(token in text for token in ("病理", "组织学")): return "histology"
    if any(token in text for token in ("分期", "晚期", "转移", "复发")): return "stage"
    if any(token in text for token in ("治疗", "化疗", "放疗", "手术", "用药")): return "prior_therapy"
    if any(token in text for token in ("肝功能", "肾功能", "血常规", "血红蛋白", "血小板", "中性粒")): return "organ_function"
    if any(token in text for token in ("感染", "心脏", "高血压", "合并症")): return "comorbidity"
    if any(token in text for token in ("癌", "肿瘤", "瘤")): return "disease"
    return "other"


def strict_structure(text: str, *, criterion_type: str, disease: str) -> tuple[str | None, str | None, str | None, str | None]:
    if criterion_type == "inclusion":
        canonical = text.replace("＞＝", "≥").replace("＜＝", "≤").replace(">=", "≥").replace("<=", "≤")
        range_match = re.search(r"年龄[^\d]{0,14}(\d{1,3})\s*(?:周?岁)?\s*[-~—至]\s*(\d{1,3})\s*周?岁", canonical)
        if range_match: return "age", "between", f"{range_match.group(1)}|{range_match.group(2)}", "year"
        min_match = re.search(r"(?:年龄[^\d]{0,14}(?:≥|不少于|大于等于)\s*(\d{1,3})\s*周?岁|年龄[^\d]{0,14}(\d{1,3})\s*周?岁\s*以上)", canonical)
        if min_match: return "age", "gte", min_match.group(1) or min_match.group(2), "year"
        max_match = re.search(r"年龄[^\d]{0,14}(?:≤|不超过|小于等于)\s*(\d{1,3})\s*周?岁", canonical)
        if max_match: return "age", "lte", max_match.group(1), "year"
        age_range_en = re.search(r"\b(?:age[sd]?|aged)\b[^\d]{0,20}(\d{1,3})\s*(?:-|–|—|to)\s*(\d{1,3})\s*years?", canonical, flags=re.I)
        if age_range_en: return "age", "between", f"{age_range_en.group(1)}|{age_range_en.group(2)}", "year"
        age_min_en = re.search(r"\b(?:age[sd]?|aged)\b[^\d]{0,20}(?:(?:≥|at least)\s*)?(\d{1,3})\s*years?\s*(?:or older|and above|or above|minimum)?", canonical, flags=re.I)
        if age_min_en and ("≥" in canonical or re.search(r"(?:or older|and above|or above|at least)", canonical, flags=re.I)):
            return "age", "gte", age_min_en.group(1), "year"
        age_max_en = re.search(r"\b(?:age[sd]?|aged)\b[^\d]{0,20}(?:≤|up to|not older than)\s*(\d{1,3})\s*years?", canonical, flags=re.I)
        if age_max_en: return "age", "lte", age_max_en.group(1), "year"
        ecog_range = re.search(r"ECOG[^\d]{0,40}(\d)\s*(?:[-~–—至或/、]|to|or)\s*(\d)", canonical, flags=re.I)
        if ecog_range: return "ecog", "lte", str(max(int(ecog_range.group(1)), int(ecog_range.group(2)))), "score"
        ecog_match = re.search(r"ECOG[^\d]{0,40}(?:≤|不超过|up to|no more than)?\s*(\d)", canonical, flags=re.I)
        if ecog_match: return "ecog", "lte", ecog_match.group(1), "score"
    return None, None, None, None


def _organization_code(name: str, top30: dict[str, dict[str, Any]]) -> str:
    if name in top30: return top30[name]["organization_code"]
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:12].upper()
    return f"CHICTR-OTHER-{digest}"


def _detail(row: sqlite3.Row) -> dict[str, Any]:
    raw = row["detail_json"] if "detail_json" in row.keys() else None
    if not raw: return {}
    try: return json.loads(raw)
    except (TypeError, json.JSONDecodeError): return {}


def _row_value(row: sqlite3.Row, key: str) -> Any:
    return row[key] if key in row.keys() else None


def build_demo(source_db: str | Path, target_db: str | Path, top30_json: str | Path) -> dict[str, Any]:
    """Build the matching database from detailed cancer trials only.

    Top30 membership remains an output label and never controls trial inclusion.
    """
    initialize(target_db)
    ranking = json.loads(Path(top30_json).read_text(encoding="utf-8")); top30 = {item["name_zh"]: item for item in ranking["institutions"]}
    source = sqlite3.connect(f"file:{Path(source_db).resolve().as_posix()}?mode=ro", uri=True); source.row_factory = sqlite3.Row
    source_rows = list(source.execute("SELECT * FROM trials ORDER BY registration_number")); source.close()
    detailed_rows = [(row, _detail(row)) for row in source_rows]
    rows = [(row, detail) for row, detail in detailed_rows if detail and is_cancer_trial(detail)]
    institutions = sorted({clean_text(row["institution"]) or "未填写实施单位" for row, _ in rows})
    detail_count = sum(bool(detail) for _, detail in detailed_rows)
    cancer_detail_count = len(rows); top30_trial_count = 0

    with connect(target_db) as connection:
        connection.execute("DELETE FROM trials")
        connection.execute("DELETE FROM organizations")
        for name in institutions:
            item = top30.get(name); code = _organization_code(name, top30)
            connection.execute("""INSERT INTO organizations(organization_code,organization_name_zh,partner_status,country_region,official_rank,official_trial_count,source_url,valid_from)
                VALUES(?,?,?,'中国',?,?,?,?) ON CONFLICT(organization_code) DO UPDATE SET organization_name_zh=excluded.organization_name_zh,
                partner_status=excluded.partner_status,official_rank=excluded.official_rank,official_trial_count=excluded.official_trial_count,
                source_url=excluded.source_url,updated_at=CURRENT_TIMESTAMP""",
                (code, name, "demo_partner" if item else "非合作", item.get("rank") if item else None, item.get("trial_count") if item else None,
                 ranking["source_url"] if item else "https://www.chictr.org.cn/", ranking["fetched_at"][:10]))
        for row, detail in rows:
            institution = clean_text(row["institution"]) or "未填写实施单位"; item = top30.get(institution)
            if item: top30_trial_count += 1
            registration = clean_text(row["registration_number"]); project_id = clean_text(_row_value(row, "project_id"))
            detail_url = clean_text(detail.get("detail_url")) or f"https://www.chictr.org.cn/showproj.html?proj={project_id}"
            full_text = " ".join(clean_text(detail.get(key)) for key in detail)
            biomarkers = "|".join(marker for marker in BIOMARKERS if marker.casefold() in full_text.casefold())
            interventions = detail.get("interventions") or []
            intervention_summary = " | ".join(clean_text(x.get("intervention") or x.get("intervention_en")) for x in interventions if isinstance(x, dict) and clean_text(x.get("intervention") or x.get("intervention_en")))
            org_id = connection.execute("SELECT id FROM organizations WHERE organization_code=?", (_organization_code(institution, top30),)).fetchone()[0]
            raw_record = {"index": dict(row), "detail": detail or None, "detail_available": bool(detail)}
            connection.execute("""INSERT INTO trials(organization_id,partner_trial_id,chictr_registration_number,title_zh,title_en,recruitment_status,
                study_type,study_phase,primary_disease,disease_aliases,biomarker_summary,intervention_summary,first_enrollment_date,planned_end_date,
                primary_sponsor,lead_institution,source_url,last_verified_date,record_status,brief_summary,data_notes,raw_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (org_id, registration, registration, clean_text(detail.get("title")) or clean_text(_row_value(row, "title")) or registration,
                 clean_text(detail.get("title_en")), normalize_recruitment(detail.get("recruitment_status")), clean_text(detail.get("study_type")) or clean_text(_row_value(row, "study_type")),
                 clean_text(detail.get("study_phase")), clean_text(detail.get("disease")) or "详情待补充", clean_text(detail.get("disease_en")), biomarkers,
                 intervention_summary, clean_text(detail.get("study_start_date")), clean_text(detail.get("study_end_date")), clean_text(detail.get("primary_sponsor")) or institution,
                 clean_text(detail.get("leader_institution")) or institution, detail_url, clean_text(detail.get("scraped_at"))[:10] or clean_text(_row_value(row, "detail_fetched_at"))[:10] or clean_text(_row_value(row, "discovered_at"))[:10],
                 "有效", clean_text(detail.get("objectives")), "ChiCTR详情尚未抓取" if not detail else "", json.dumps(raw_record, ensure_ascii=False)))
            trial_id = connection.execute("SELECT last_insert_rowid()").fetchone()[0]
            connection.execute("INSERT INTO trial_sites(trial_id,site_id,site_name,recruitment_status,last_verified_date,record_status) VALUES(?,?,?,?,?,'有效')",
                (trial_id, "LEAD", institution, normalize_recruitment(detail.get("recruitment_status")), clean_text(detail.get("scraped_at"))[:10] or clean_text(_row_value(row, "detail_fetched_at"))[:10]))
            for index, intervention in enumerate(interventions, start=1):
                if not isinstance(intervention, dict): continue
                connection.execute("INSERT INTO trial_cohorts(trial_id,cohort_id,cohort_name,cohort_status,intervention_name,intervention_type,arm_description,record_status) VALUES(?,?,?,?,?,?,?,'有效')",
                    (trial_id, f"ARM-{index:02d}", clean_text(intervention.get("group")) or f"组别{index}", normalize_recruitment(detail.get("recruitment_status")), clean_text(intervention.get("intervention")), "其他", clean_text(intervention.get("intervention_en"))))
            for criterion_type, text_key, en_key, prefix, missing_text in (("inclusion", "inclusion_criteria", "inclusion_criteria_en", "INC", "ChiCTR详情或入选标准尚未获取"), ("exclusion", "exclusion_criteria", "exclusion_criteria_en", "EXC", "ChiCTR详情或排除标准尚未获取")):
                zh_parts = split_criteria(detail.get(text_key)) or [missing_text]; en_parts = split_criteria(detail.get(en_key)); disease = clean_text(detail.get("disease"))
                for order, text in enumerate(zh_parts, start=1):
                    field, operator, value, unit = strict_structure(text, criterion_type=criterion_type, disease=disease)
                    connection.execute("""INSERT INTO eligibility_criteria(trial_id,cohort_id,criterion_type,criterion_id,criterion_order,criterion_category,
                        criterion_text_zh,criterion_text_en,is_mandatory,structured_field,operator,structured_value,unit,normalization_note,record_status)
                        VALUES(?,'ALL',?,?,?,?,?,?,1,?,?,?,?,?,'有效')""",
                        (trial_id, criterion_type, f"{prefix}-{order:03d}", order, category_for(text), text, en_parts[order-1] if order <= len(en_parts) else None,
                         field, operator, value, unit, "从ChiCTR文本严格解析" if field else "自由文本或详情缺失，须模型/人工复核"))
        tokenizer = replace_trial_search(connection)
        metadata = {"demo_source_as_of": ranking["fetched_at"], "demo_source_db": str(Path(source_db).resolve()),
                    "demo_selection": "detail_available_and_cancer_related", "snapshot_index_trials": len(source_rows),
                    "snapshot_detail_trials": detail_count, "top30_snapshot_trials": top30_trial_count, "snapshot_cancer_detail_trials": cancer_detail_count}
        for key, value in metadata.items():
            connection.execute("INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    return {"partner_organizations": sum(name in top30 for name in institutions), "snapshot_trials_scanned": len(source_rows), "detail_trials_scanned": detail_count,
            "snapshot_trials_imported": len(rows), "top30_partner_trials": top30_trial_count, "non_partner_trials": len(rows)-top30_trial_count,
            "cancer_detail_trials": cancer_detail_count, "fts_tokenizer": tokenizer}
