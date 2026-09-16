from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .database import connect, initialize, replace_trial_search
from .demo_builder import category_for, strict_structure
from .normalization import clean_text
from .registry_importer import _code, _ensure_organization, _integer, _upsert_trial
from .workbook_v3 import CATEGORY_MAP, MAIN_MAP


SHEET_NAME = "单试验填写表"
TEMPLATE_VERSION = "中国临床试验合作方单试验模板-第4版"
REQUIRED = {
    "模板版本", "合作机构编码", "合作机构名称", "合作状态", "提交批次号", "数据截至日期",
    "合作方试验编号", "记录状态", "主注册库", "主注册号", "试验公开名称", "招募状态",
    "研究类型", "主要疾病或研究人群", "国家或地区", "最后核验日期", "主办单位", "来源链接",
}


def _value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return value


def _form_values(sheet: Any) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for row in range(1, 44):
        for label_column, value_column in ((1, 2), (5, 6)):
            label = clean_text(sheet.cell(row=row, column=label_column).value).rstrip("*")
            if label:
                values[label] = _value(sheet.cell(row=row, column=value_column).value)
    return values


def _table_rows(sheet: Any, header_row: int, end_row: int) -> list[dict[str, Any]]:
    headers = [clean_text(sheet.cell(row=header_row, column=column).value).rstrip("*") for column in range(1, 9)]
    rows: list[dict[str, Any]] = []
    for row_number in range(header_row + 1, end_row + 1):
        row = {header: _value(sheet.cell(row=row_number, column=index + 1).value) for index, header in enumerate(headers) if header}
        if any(value not in (None, "") for value in row.values()):
            row["_row_number"] = row_number
            rows.append(row)
    return rows


def _region(value: Any) -> tuple[str, str, str]:
    parts = [clean_text(part) for part in str(value or "").split("｜")]
    parts.extend([""] * (3 - len(parts)))
    return parts[0], parts[1], parts[2]


def import_workbook_v4(db_path: str | Path, workbook_path: str | Path, workbook: Any) -> dict[str, int]:
    if SHEET_NAME not in workbook.sheetnames:
        raise ValueError(f"缺少工作表：{SHEET_NAME}")
    sheet = workbook[SHEET_NAME]
    form = _form_values(sheet)
    if clean_text(form.get("模板版本")) != TEMPLATE_VERSION:
        raise ValueError(f"模板版本必须为{TEMPLATE_VERSION}")
    if missing := sorted(label for label in REQUIRED if form.get(label) in (None, "")):
        raise ValueError(f"单试验填写表缺少必填值：{missing}")
    source_url = clean_text(form.get("来源链接"))
    if not source_url.casefold().startswith(("http://", "https://")):
        raise ValueError("来源链接必须是HTTP(S)地址")

    tables = {
        "注册号": _table_rows(sheet, 46, 50),
        "队列": _table_rows(sheet, 54, 60),
        "中心": _table_rows(sheet, 64, 70),
        "标准": _table_rows(sheet, 74, 103),
        "联系人": _table_rows(sheet, 107, 111),
        "结局": _table_rows(sheet, 115, 119),
        "伦理": _table_rows(sheet, 123, 127),
    }
    standard_types = {clean_text(row.get("标准类型")) for row in tables["标准"]}
    if not {"入选", "排除"}.issubset(standard_types):
        raise ValueError("入排标准至少需要一条完整入选标准和一条完整排除标准")

    organization_code = clean_text(form["合作机构编码"])
    organization_name = clean_text(form["合作机构名称"])
    partner_status = clean_text(form["合作状态"])
    batch_name = clean_text(form["提交批次号"])
    as_of = clean_text(form["数据截至日期"])
    partner_trial_id = clean_text(form["合作方试验编号"])
    primary_registry = clean_text(form["主注册库"])
    primary_registry_id = clean_text(form["主注册号"])
    chictr = clean_text(form.get("ChiCTR注册号"))
    if not chictr and primary_registry_id.casefold().startswith("chictr"):
        chictr = primary_registry_id

    registry_ids = [{
        "registry_source": primary_registry, "registry_id": primary_registry_id,
        "id_type": "主注册号", "is_primary": True, "source_url": source_url,
    }]
    if chictr and chictr.casefold() != primary_registry_id.casefold():
        registry_ids.append({"registry_source": "ChiCTR", "registry_id": chictr, "id_type": "次级注册号", "is_primary": False, "source_url": source_url})
    for row in tables["注册号"]:
        registry_id = clean_text(row.get("注册号"))
        if not registry_id or any(item["registry_id"].casefold() == registry_id.casefold() for item in registry_ids):
            continue
        registry_ids.append({
            "registry_source": clean_text(row.get("注册库")), "registry_id": registry_id,
            "id_type": clean_text(row.get("编号类型")), "is_primary": clean_text(row.get("是否主注册号")) == "是",
            "source_url": clean_text(row.get("来源链接")) or source_url,
        })

    trial = {field: form.get(header) for header, field in MAIN_MAP.items()}
    trial.update({
        "partner_trial_id": partner_trial_id, "primary_registry": primary_registry,
        "chictr_registration_number": chictr or None,
        "other_registry_ids": clean_text(form.get("其他注册号")),
        "target_enrollment": _integer(form.get("计划入组人数")),
        "record_status": clean_text(form.get("记录状态")) or "有效",
    })
    for field in ("minimum_age_years", "maximum_age_years", "maximum_ecog"):
        trial[field] = float(trial[field]) if trial.get(field) not in (None, "") else None

    counts = {"试验": 0, "注册号": 0, "研究中心": 0, "队列": 0, "干预": 0, "入选标准": 0, "排除标准": 0, "联系人": 0, "结局": 0, "伦理": 0}
    initialize(db_path)
    with connect(db_path) as connection:
        organization_id = _ensure_organization(connection, organization_name, partner_status, source_url, organization_code)
        connection.execute(
            """INSERT INTO import_batches(organization_id,submission_batch_id,data_as_of_date,template_version,source_file,row_counts_json)
               VALUES(?,?,?,?,?,'{}') ON CONFLICT(organization_id,submission_batch_id) DO UPDATE SET
               data_as_of_date=excluded.data_as_of_date,template_version=excluded.template_version,
               source_file=excluded.source_file,imported_at=CURRENT_TIMESTAMP""",
            (organization_id, batch_name, as_of, TEMPLATE_VERSION, str(Path(workbook_path).resolve())),
        )
        batch_id = int(connection.execute("SELECT id FROM import_batches WHERE organization_id=? AND submission_batch_id=?", (organization_id, batch_name)).fetchone()[0])
        raw = {"source_format": "PARTNER_WORKBOOK_V4_SINGLE_TRIAL", "form": form, "tables": tables}
        trial_id = _upsert_trial(connection, organization_id, trial, registry_ids, raw)
        connection.execute("UPDATE trials SET import_batch_id=? WHERE id=?", (batch_id, trial_id))
        counts["试验"] = 1; counts["注册号"] = len(registry_ids)

        for index, row in enumerate(tables["队列"], start=1):
            cohort_id = clean_text(row.get("队列编号")) or f"COHORT-{index:03d}"
            name = clean_text(row.get("队列名称")) or cohort_id
            connection.execute(
                "INSERT INTO trial_cohorts(trial_id,cohort_id,cohort_name,cohort_status,disease_scope,biomarker_scope,intervention_name,arm_description,target_enrollment,record_status) VALUES(?,?,?,?,?,?,?,?,?,'有效')",
                (trial_id, cohort_id, name, clean_text(row.get("队列状态")), clean_text(row.get("疾病范围")), clean_text(row.get("标志物范围")), clean_text(row.get("干预名称")), clean_text(row.get("队列说明")), _integer(row.get("计划人数"))),
            )
            if intervention := clean_text(row.get("干预名称")):
                connection.execute("INSERT INTO trial_interventions(trial_id,cohort_id,intervention_name,description) VALUES(?,?,?,?)", (trial_id, cohort_id, intervention, clean_text(row.get("队列说明"))))
                counts["干预"] += 1
            counts["队列"] += 1

        for index, row in enumerate(tables["中心"], start=1):
            country, province, city = _region(row.get("地区（国家｜省｜市）"))
            evidence = clean_text(row.get("证据类型")) or "合作方报告中心"
            site_name = clean_text(row.get("中心名称")) or (f"{country}（仅国家记录，非已核验中心）" if country else "未填写中心")
            connection.execute(
                """INSERT INTO trial_sites(trial_id,site_id,site_name,country,province,city,recruitment_status,principal_investigator,
                   contact_channel,evidence_type,last_verified_date,record_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,'有效')""",
                (trial_id, clean_text(row.get("中心编号")) or f"SITE-{index:03d}", site_name, country, province, city,
                 clean_text(row.get("招募状态")), clean_text(row.get("主要研究者")), clean_text(row.get("联系方式")), evidence,
                 clean_text(row.get("最后核验日期"))),
            )
            counts["研究中心"] += 1

        for order, row in enumerate(tables["标准"], start=1):
            kind_zh = clean_text(row.get("标准类型")); kind = "inclusion" if kind_zh == "入选" else "exclusion" if kind_zh == "排除" else ""
            text = clean_text(row.get("标准原文"))
            if not kind or not text:
                raise ValueError(f"入排标准第{row['_row_number']}行必须填写标准类型和完整标准原文")
            structured = clean_text(row.get("结构化表达（可选）"))
            field, operator, value, unit = strict_structure(structured or text, criterion_type=kind, disease=clean_text(form.get("主要疾病或研究人群")))
            category_raw = clean_text(row.get("类别"))
            connection.execute(
                """INSERT INTO eligibility_criteria(trial_id,cohort_id,criterion_type,criterion_id,criterion_order,criterion_category,
                   criterion_text_zh,is_mandatory,structured_field,operator,structured_value,unit,normalization_note,record_status)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'有效')""",
                (trial_id, clean_text(row.get("适用队列")) or "ALL", kind, clean_text(row.get("标准编号")) or f"{kind[:3].upper()}-{order:03d}", order,
                 CATEGORY_MAP.get(category_raw, category_raw) or category_for(text), text, 0 if clean_text(row.get("是否必需")) == "否" else 1,
                 field or None, operator or None, value or None, unit or None, "第4版单页模板原文；结构化表达可选且仅可靠模式生效"),
            )
            counts["入选标准" if kind == "inclusion" else "排除标准"] += 1

        for row in tables["联系人"]:
            country, province, city = _region(row.get("地区"))
            connection.execute(
                "INSERT INTO trial_contacts(trial_id,contact_type,contact_name,affiliation,country,province,city,address,telephone,email) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (trial_id, clean_text(row.get("联系人类型")), clean_text(row.get("姓名")), clean_text(row.get("单位")), country, province, city,
                 clean_text(row.get("地址")), clean_text(row.get("电话")), clean_text(row.get("邮箱"))),
            )
            counts["联系人"] += 1

        for index, row in enumerate(tables["结局"], start=1):
            text = clean_text(row.get("指标原文"))
            if not text: continue
            outcome_type = "primary" if clean_text(row.get("指标类型")) == "主要" else "secondary"
            connection.execute("INSERT INTO trial_outcomes(trial_id,outcome_type,outcome_order,outcome_text) VALUES(?,?,?,?)", (trial_id, outcome_type, int(row.get("序号") or index), text))
            counts["结局"] += 1

        for row in tables["伦理"]:
            connection.execute(
                "INSERT OR IGNORE INTO trial_ethics_reviews(trial_id,review_status,approval_date,committee_name,contact_name,contact_channel) VALUES(?,?,?,?,?,?)",
                (trial_id, clean_text(row.get("审查状态")), clean_text(row.get("批准日期")), clean_text(row.get("伦理委员会")), clean_text(row.get("联系人")), clean_text(row.get("联系方式"))),
            )
            counts["伦理"] += 1

        connection.execute("UPDATE import_batches SET row_counts_json=? WHERE id=?", (json.dumps(counts, ensure_ascii=False), batch_id))
        replace_trial_search(connection)
    return counts
