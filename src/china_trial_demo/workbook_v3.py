from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .database import connect, initialize, replace_trial_search
from .demo_builder import category_for, strict_structure
from .normalization import clean_text
from .registry_importer import _code, _ensure_organization, _integer, _upsert_trial


TEMPLATE_VERSION = "中国临床试验合作方模板-第3版"
MAIN_SHEET = "试验主表"

MAIN_MAP = {
    "合作方试验编号": "partner_trial_id", "主注册库": "primary_registry", "ChiCTR注册号": "chictr_registration_number",
    "UTRN": "utrn", "方案编号": "protocol_number", "其他注册号": "other_registry_ids", "试验公开名称": "title_zh",
    "试验英文名称": "title_en", "科学名称": "scientific_title", "招募状态": "recruitment_status", "研究类型": "study_type",
    "研究设计": "study_design", "研究阶段": "study_phase", "主要疾病或研究人群": "primary_disease",
    "疾病别名": "disease_aliases", "病理类型": "histology", "疾病分期或状态": "disease_stage",
    "关键分子标志物": "biomarker_summary", "干预概述": "intervention_summary", "计划入组人数": "target_enrollment",
    "计划入组说明": "target_enrollment_text", "性别限制": "sex", "最小年龄（岁）": "minimum_age_years",
    "最大年龄（岁）": "maximum_age_years", "最大ECOG评分": "maximum_ecog", "开始入组日期": "first_enrollment_date",
    "计划结束日期": "planned_end_date", "注册日期": "registration_date", "最后更新日期": "last_update_date",
    "国家或地区": "countries", "主办单位": "primary_sponsor", "组长单位": "lead_institution", "来源链接": "source_url",
    "最后核验日期": "last_verified_date", "记录状态": "record_status", "研究摘要": "brief_summary", "数据备注": "data_notes",
}
REQUIRED_MAIN = {"模板版本", "合作机构编码", "合作机构名称", "合作状态", "提交批次号", "数据截至日期",
                 "合作方试验编号", "试验公开名称", "招募状态", "主要疾病或研究人群", "主办单位", "来源链接", "最后核验日期", "记录状态"}
CATEGORY_MAP = {"疾病":"disease", "病理":"histology", "分期":"stage", "生物标志物":"biomarker", "年龄":"age", "性别":"sex",
                "体能状态":"ecog", "既往治疗":"prior_therapy", "治疗线数":"treatment_line", "器官功能":"organ_function",
                "实验室":"lab", "合并症":"comorbidity", "妊娠":"pregnancy", "其他":"other"}
FIELD_MAP = {"年龄":"age", "性别":"sex", "体能状态":"ecog", "疾病":"cancer_type", "病理":"histology", "分期":"disease_stage",
             "生物标志物":"biomarkers", "突变":"biomarkers", "既往治疗":"prior_therapies", "治疗线数":"treatment_lines_completed",
             "合并症":"comorbidities", "妊娠":"pregnant"}
OPERATOR_MAP = {"等于":"eq", "不等于":"neq", "属于":"in", "不属于":"not_in", "大于等于":"gte", "小于等于":"lte",
                "介于":"between", "包含":"contains", "不包含":"not_contains", "已提供":"exists", "未提供":"not_exists"}


def _rows(sheet: Any) -> list[dict[str, Any]]:
    headers = [clean_text(cell.value) for cell in sheet[3]]
    rows: list[dict[str, Any]] = []
    for row_number, values in enumerate(sheet.iter_rows(min_row=4, values_only=True), start=4):
        row = {headers[index]: values[index] for index in range(min(len(headers), len(values))) if headers[index]}
        if not any(value not in (None, "") for value in row.values()): continue
        if clean_text(row.get("合作方试验编号")) == "示例试验-001": continue
        row["_row_number"] = row_number; rows.append(row)
    return rows


def _child_rows(workbook: Any, name: str) -> list[dict[str, Any]]:
    return _rows(workbook[name]) if name in workbook.sheetnames else []


def import_workbook_v3(db_path: str | Path, workbook_path: str | Path, workbook: Any) -> dict[str, int]:
    if MAIN_SHEET not in workbook.sheetnames: raise ValueError(f"缺少工作表：{MAIN_SHEET}")
    main_rows = _rows(workbook[MAIN_SHEET])
    if not main_rows: raise ValueError("试验主表没有可导入的数据行")
    headers = {clean_text(cell.value) for cell in workbook[MAIN_SHEET][3]}
    if missing_headers := sorted(REQUIRED_MAIN - headers): raise ValueError(f"试验主表缺少列：{missing_headers}")
    for row in main_rows:
        if clean_text(row.get("模板版本")) != TEMPLATE_VERSION: raise ValueError(f"模板版本必须为{TEMPLATE_VERSION}")
        if missing := sorted(name for name in REQUIRED_MAIN if row.get(name) in (None, "")):
            raise ValueError(f"试验主表第{row['_row_number']}行缺少必填值：{missing}")
        if not clean_text(row.get("来源链接")).casefold().startswith(("http://", "https://")):
            raise ValueError(f"试验主表第{row['_row_number']}行来源链接必须是HTTP(S)地址")
    identities = {(clean_text(r["合作机构编码"]), clean_text(r["合作机构名称"]), clean_text(r["合作状态"]), clean_text(r["提交批次号"]), str(r["数据截至日期"])) for r in main_rows}
    if len(identities) != 1: raise ValueError("同一工作簿必须属于一家合作机构和一个提交批次")
    if len({clean_text(r["合作方试验编号"]) for r in main_rows}) != len(main_rows): raise ValueError("合作方试验编号重复")
    organization_code, organization_name, partner_status, batch_name, as_of = next(iter(identities))
    children = {name: _child_rows(workbook, name) for name in ("注册号", "研究中心", "队列与干预", "入排标准", "联系人", "结局指标", "伦理审查")}
    valid_trials = {clean_text(row["合作方试验编号"]) for row in main_rows}
    for sheet_name, rows in children.items():
        for row in rows:
            key = clean_text(row.get("合作方试验编号"))
            if key and key not in valid_trials: raise ValueError(f"{sheet_name}第{row['_row_number']}行引用了不存在的合作方试验编号：{key}")
    counts = {"试验": 0, "注册号": 0, "研究中心": 0, "队列": 0, "干预": 0, "入选标准": 0, "排除标准": 0, "联系人": 0, "结局": 0, "伦理": 0}
    initialize(db_path)
    with connect(db_path) as connection:
        organization_id = _ensure_organization(connection, organization_name, partner_status, "", organization_code)
        connection.execute("""INSERT INTO import_batches(organization_id,submission_batch_id,data_as_of_date,template_version,source_file,row_counts_json)
            VALUES(?,?,?,?,?,'{}') ON CONFLICT(organization_id,submission_batch_id) DO UPDATE SET data_as_of_date=excluded.data_as_of_date,
            template_version=excluded.template_version,source_file=excluded.source_file,imported_at=CURRENT_TIMESTAMP""",
            (organization_id, batch_name, as_of, TEMPLATE_VERSION, str(Path(workbook_path).resolve())))
        batch_id = int(connection.execute("SELECT id FROM import_batches WHERE organization_id=? AND submission_batch_id=?", (organization_id, batch_name)).fetchone()[0])
        id_map: dict[str, int] = {}
        for row in main_rows:
            key = clean_text(row["合作方试验编号"])
            registry_rows = [item for item in children["注册号"] if clean_text(item.get("合作方试验编号")) == key]
            registry_ids = [{"registry_source": clean_text(item.get("注册库")), "registry_id": clean_text(item.get("注册号")),
                "id_type": clean_text(item.get("编号类型")), "is_primary": clean_text(item.get("是否主注册号")) in {"是", "1", "true"}, "source_url": clean_text(item.get("来源链接"))}
                for item in registry_rows if clean_text(item.get("注册号"))]
            chictr = clean_text(row.get("ChiCTR注册号"))
            if chictr and not any(item["registry_id"].casefold() == chictr.casefold() for item in registry_ids):
                registry_ids.append({"registry_source": "ChiCTR", "registry_id": chictr, "id_type": "main_id", "is_primary": True, "source_url": clean_text(row.get("来源链接"))})
            if not registry_ids:
                registry_ids = [{"registry_source": clean_text(row.get("主注册库")) or "partner", "registry_id": key, "id_type": "partner_id", "is_primary": True, "source_url": clean_text(row.get("来源链接"))}]
            trial = {field: row.get(header) for header, field in MAIN_MAP.items()}
            trial["target_enrollment"] = _integer(trial.get("target_enrollment")); trial["record_status"] = clean_text(trial.get("record_status")) or "有效"
            for field in ("minimum_age_years", "maximum_age_years", "maximum_ecog"):
                trial[field] = float(trial[field]) if trial.get(field) not in (None, "") else None
            trial_id = _upsert_trial(connection, organization_id, trial, registry_ids, {"source_format": "PARTNER_WORKBOOK_V3", "main_row": {k:v for k,v in row.items() if k != "_row_number"}})
            connection.execute("UPDATE trials SET import_batch_id=? WHERE id=?", (batch_id, trial_id)); id_map[key] = trial_id
            counts["试验"] += 1; counts["注册号"] += len(registry_ids)
        for row in children["研究中心"]:
            trial_id = id_map[clean_text(row["合作方试验编号"])]
            connection.execute("""INSERT INTO trial_sites(trial_id,site_id,site_name,country,province,city,district,recruitment_status,principal_investigator,
                contact_channel,address,evidence_type,last_verified_date,record_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'有效')""",
                (trial_id, clean_text(row.get("中心编号")) or _code(str(counts["研究中心"])), clean_text(row.get("中心名称")) or "未填写中心",
                 clean_text(row.get("国家")), clean_text(row.get("省")), clean_text(row.get("市")), clean_text(row.get("区县")), clean_text(row.get("招募状态")),
                 clean_text(row.get("主要研究者")), clean_text(row.get("联系方式")), clean_text(row.get("地址")), clean_text(row.get("证据类型")) or "partner_reported",
                 clean_text(row.get("最后核验日期"))))
            counts["研究中心"] += 1
        for row in children["队列与干预"]:
            trial_id = id_map[clean_text(row["合作方试验编号"])]
            cohort_id = clean_text(row.get("队列编号")) or f"COHORT-{counts['队列']+1:03d}"
            connection.execute("INSERT INTO trial_cohorts(trial_id,cohort_id,cohort_name,cohort_status,disease_scope,biomarker_scope,intervention_name,intervention_type,arm_description,target_enrollment,record_status) VALUES(?,?,?,?,?,?,?,?,?,?,'有效')",
                (trial_id, cohort_id, clean_text(row.get("队列名称")) or cohort_id, clean_text(row.get("队列状态")), clean_text(row.get("疾病范围")),
                 clean_text(row.get("标志物范围")), clean_text(row.get("干预名称")), clean_text(row.get("干预类型")), clean_text(row.get("队列说明")), _integer(row.get("计划人数"))))
            if clean_text(row.get("干预名称")):
                connection.execute("INSERT INTO trial_interventions(trial_id,cohort_id,intervention_name,intervention_type,description) VALUES(?,?,?,?,?)",
                    (trial_id, cohort_id, clean_text(row.get("干预名称")), clean_text(row.get("干预类型")), clean_text(row.get("队列说明"))))
                counts["干预"] += 1
            counts["队列"] += 1
        for row in children["入排标准"]:
            trial_id = id_map[clean_text(row["合作方试验编号"])]; kind_zh = clean_text(row.get("标准类型")); kind = "inclusion" if kind_zh == "入选" else "exclusion" if kind_zh == "排除" else ""
            if not kind: raise ValueError(f"入排标准第{row['_row_number']}行标准类型必须为入选或排除")
            text = clean_text(row.get("标准原文")); order = int(row.get("序号") or 1)
            category_raw = clean_text(row.get("类别")); field_raw = clean_text(row.get("患者字段")); operator_raw = clean_text(row.get("比较关系"))
            field = FIELD_MAP.get(field_raw, field_raw); operator = OPERATOR_MAP.get(operator_raw, operator_raw); value = clean_text(row.get("比较值")); unit = clean_text(row.get("单位"))
            if not field and not operator:
                parsed_field, parsed_operator, parsed_value, parsed_unit = strict_structure(text, criterion_type=kind, disease="")
                field, operator, value, unit = parsed_field or "", parsed_operator or "", parsed_value or "", parsed_unit or ""
            connection.execute("""INSERT INTO eligibility_criteria(trial_id,cohort_id,criterion_type,criterion_id,criterion_order,criterion_category,criterion_text_zh,criterion_text_en,
                is_mandatory,structured_field,operator,structured_value,unit,normalization_note,record_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,'有效')""",
                (trial_id, clean_text(row.get("队列编号")) or "ALL", kind, clean_text(row.get("标准编号")) or f"{kind[:3].upper()}-{order:03d}", order,
                 CATEGORY_MAP.get(category_raw, category_raw) or category_for(text), text, clean_text(row.get("英文原文")), 0 if clean_text(row.get("是否必需")) == "否" else 1,
                 field or None, operator or None, value or None, unit or None, "合作方模板原文；结构化字段仅在明确时使用"))
            counts["入选标准" if kind == "inclusion" else "排除标准"] += 1
        for row in children["联系人"]:
            trial_id = id_map[clean_text(row["合作方试验编号"])]
            connection.execute("INSERT INTO trial_contacts(trial_id,contact_type,contact_name,affiliation,country,province,city,address,telephone,email) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (trial_id, clean_text(row.get("联系人类型")), clean_text(row.get("姓名")), clean_text(row.get("单位")), clean_text(row.get("国家")),
                 clean_text(row.get("省")), clean_text(row.get("市")), clean_text(row.get("地址")), clean_text(row.get("电话")), clean_text(row.get("邮箱"))))
            counts["联系人"] += 1
        for row in children["结局指标"]:
            trial_id = id_map[clean_text(row["合作方试验编号"])]; outcome_type = "primary" if clean_text(row.get("指标类型")) == "主要" else "secondary"
            connection.execute("INSERT INTO trial_outcomes(trial_id,outcome_type,outcome_order,outcome_text) VALUES(?,?,?,?)",
                (trial_id, outcome_type, int(row.get("序号") or 1), clean_text(row.get("指标原文"))))
            counts["结局"] += 1
        for row in children["伦理审查"]:
            trial_id = id_map[clean_text(row["合作方试验编号"])]
            connection.execute("INSERT OR IGNORE INTO trial_ethics_reviews(trial_id,review_status,approval_date,committee_name,contact_name,contact_channel) VALUES(?,?,?,?,?,?)",
                (trial_id, clean_text(row.get("审查状态")), clean_text(row.get("批准日期")), clean_text(row.get("伦理委员会")), clean_text(row.get("联系人")), clean_text(row.get("联系方式"))))
            counts["伦理"] += 1
        connection.execute("UPDATE import_batches SET row_counts_json=? WHERE id=?", (json.dumps(counts, ensure_ascii=False), batch_id)); replace_trial_search(connection)
    return counts
