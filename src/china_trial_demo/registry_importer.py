from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable

from .database import connect, initialize, replace_trial_search
from .demo_builder import category_for, normalize_recruitment, split_criteria, strict_structure
from .normalization import clean_text


def _code(name: str, prefix: str = "REGISTRY") -> str:
    return f"{prefix}-{hashlib.sha1(name.encode('utf-8')).hexdigest()[:12].upper()}"


def _text(element: ET.Element | None, path: str) -> str:
    if element is None:
        return ""
    found = element.find(path)
    return clean_text(found.text if found is not None else "")


def _raw_text(element: ET.Element | None, path: str) -> str:
    if element is None:
        return ""
    found = element.find(path)
    return str(found.text or "").strip() if found is not None else ""


def _integer(value: Any) -> int | None:
    match = re.search(r"\d+", clean_text(value))
    return int(match.group()) if match else None


def _date(value: Any) -> str | None:
    text = clean_text(value)
    if not text or text == "1900-01-01":
        return None
    for pattern in (r"^(\d{4})-(\d{2})-(\d{2})$", r"^(\d{2})/(\d{2})/(\d{4})$"):
        match = re.match(pattern, text)
        if match:
            if pattern.startswith("^(\\d{4}"):
                return text
            day, month, year = match.groups()
            return f"{year}-{month}-{day}"
    return text


def _xml_dict(element: ET.Element) -> Any:
    if not list(element):
        return str(element.text or "").strip()
    grouped: dict[str, list[Any]] = {}
    for child in element:
        grouped.setdefault(child.tag, []).append(_xml_dict(child))
    return {key: values[0] if len(values) == 1 else values for key, values in grouped.items()}


def _ensure_organization(connection: Any, name: str, partner_status: str, source_url: str, code: str | None = None) -> int:
    organization_code = code or _code(name)
    connection.execute(
        """INSERT INTO organizations(organization_code,organization_name_zh,organization_name_en,partner_status,country_region,source_url)
           VALUES(?,?,?,?, '中国',?) ON CONFLICT(organization_code) DO UPDATE SET
           organization_name_zh=excluded.organization_name_zh,organization_name_en=excluded.organization_name_en,
           partner_status=excluded.partner_status,source_url=excluded.source_url,updated_at=CURRENT_TIMESTAMP""",
        (organization_code, name, name, partner_status, source_url),
    )
    return int(connection.execute("SELECT id FROM organizations WHERE organization_code=?", (organization_code,)).fetchone()[0])


def _find_trial(connection: Any, registry_ids: Iterable[str]) -> int | None:
    ids = [clean_text(item) for item in registry_ids if clean_text(item)]
    for registry_id in ids:
        row = connection.execute("SELECT trial_id FROM trial_registry_ids WHERE registry_id=? COLLATE NOCASE LIMIT 1", (registry_id,)).fetchone()
        if row:
            return int(row[0])
    return None


def _replace_children(connection: Any, trial_id: int) -> None:
    for table in ("trial_registry_ids", "trial_sites", "trial_contacts", "trial_cohorts", "trial_interventions",
                  "eligibility_criteria", "trial_outcomes", "trial_ethics_reviews", "trial_support_sources"):
        connection.execute(f"DELETE FROM {table} WHERE trial_id=?", (trial_id,))


def _upsert_trial(connection: Any, organization_id: int, trial: dict[str, Any], registry_ids: list[dict[str, Any]], raw: dict[str, Any]) -> int:
    known_ids = [str(item.get("registry_id") or "") for item in registry_ids]
    trial_id = _find_trial(connection, known_ids)
    fields = [
        "organization_id", "partner_trial_id", "protocol_number", "chictr_registration_number", "other_registry_ids",
        "primary_registry", "utrn", "title_zh", "title_en", "scientific_title", "recruitment_status", "study_type",
        "study_design", "study_phase", "primary_disease", "disease_aliases", "histology", "disease_stage",
        "biomarker_summary", "intervention_summary", "target_enrollment", "target_enrollment_text", "actual_enrollment",
        "sex", "minimum_age_years", "maximum_age_years", "maximum_ecog", "first_enrollment_date", "planned_end_date",
        "registration_date", "last_update_date", "countries", "primary_sponsor", "lead_institution", "source_url",
        "last_verified_date", "record_status", "brief_summary", "data_notes", "raw_json",
    ]
    values = [organization_id] + [trial.get(field) for field in fields[1:-1]] + [json.dumps(raw, ensure_ascii=False)]
    if trial_id is None:
        connection.execute(f"INSERT INTO trials({','.join(fields)}) VALUES({','.join('?' for _ in fields)})", values)
        trial_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
    else:
        connection.execute(f"UPDATE trials SET {','.join(field+'=?' for field in fields)},updated_at=CURRENT_TIMESTAMP WHERE id=?", [*values, trial_id])
        _replace_children(connection, trial_id)
    for item in registry_ids:
        registry_id = clean_text(item.get("registry_id"))
        if not registry_id:
            continue
        connection.execute(
            "INSERT INTO trial_registry_ids(trial_id,registry_source,registry_id,id_type,is_primary,source_url) VALUES(?,?,?,?,?,?)",
            (trial_id, clean_text(item.get("registry_source")), registry_id, clean_text(item.get("id_type")), int(bool(item.get("is_primary"))), clean_text(item.get("source_url"))),
        )
    return trial_id


def import_chictr_xml(db_path: str | Path, xml_path: str | Path, *, organization_code: str | None = None,
                      organization_name: str | None = None, partner_status: str = "合作") -> dict[str, Any]:
    initialize(db_path)
    root = ET.parse(xml_path).getroot()
    if root.tag != "trials":
        raise ValueError("XML根节点必须为trials")
    counts = {"trials": 0, "criteria": 0, "contacts": 0, "countries": 0, "cohorts": 0}
    with connect(db_path) as connection:
        for node in root.findall("trial"):
            main = node.find("main")
            registry_id = _text(main, "trial_id")
            if not registry_id:
                raise ValueError("XML试验缺少main/trial_id")
            source_url = _text(main, "url")
            sponsor = organization_name or _text(main, "primary_sponsor") or "未填写主办单位"
            organization_id = _ensure_organization(connection, sponsor, partner_status, source_url, organization_code)
            other_ids = []
            for item in node.findall("secondary_ids/secondary_id"):
                value = _text(item, "sec_id")
                if value:
                    other_ids.append({"registry_source": _text(item, "issuing_authority") or "secondary", "registry_id": value,
                                      "id_type": "secondary_id", "is_primary": 0, "source_url": source_url})
            registry_ids = [{"registry_source": _text(main, "reg_name") or "ChiCTR", "registry_id": registry_id,
                             "id_type": "main_id", "is_primary": 1, "source_url": source_url}, *other_ids]
            countries = [clean_text(item.text) for item in node.findall("countries/country2") if clean_text(item.text)]
            disease = _text(main, "hc_freetext") or _text(main, "public_title")
            trial = {
                "partner_trial_id": registry_id, "protocol_number": None,
                "chictr_registration_number": registry_id if registry_id.casefold().startswith("chictr") else None,
                "other_registry_ids": "|".join(item["registry_id"] for item in other_ids), "primary_registry": _text(main, "reg_name"),
                "utrn": _text(main, "utrn"), "title_zh": _text(main, "public_title") or registry_id,
                "title_en": _text(main, "public_title"), "scientific_title": _text(main, "scientific_title"),
                "recruitment_status": normalize_recruitment(_text(main, "recruitment_status")), "study_type": _text(main, "study_type"),
                "study_design": _text(main, "study_design"), "study_phase": _text(main, "phase"), "primary_disease": disease,
                "disease_aliases": "", "histology": "", "disease_stage": "", "biomarker_summary": "",
                "intervention_summary": _text(main, "i_freetext"), "target_enrollment": _integer(_text(main, "target_size")),
                "target_enrollment_text": _text(main, "target_size"), "actual_enrollment": _integer(_text(main, "results_actual_enrolment")),
                "sex": _text(node.find("criteria"), "gender"), "minimum_age_years": _integer(_text(node.find("criteria"), "agemin")),
                "maximum_age_years": _integer(_text(node.find("criteria"), "agemax")), "maximum_ecog": None,
                "first_enrollment_date": _date(_text(main, "date_enrolment")), "planned_end_date": _date(_text(main, "results_date_completed")),
                "registration_date": _date(_text(main, "date_registration")), "last_update_date": None, "countries": "|".join(countries),
                "primary_sponsor": _text(main, "primary_sponsor"), "lead_institution": _text(main, "primary_sponsor"),
                "source_url": source_url, "last_verified_date": _date(_text(main, "date_registration")), "record_status": "有效",
                "brief_summary": "", "data_notes": "WHO ICTRP XML导入",
            }
            trial_id = _upsert_trial(connection, organization_id, trial, registry_ids, {"source_format": "WHO_ICTRP_XML", "trial": _xml_dict(node)})
            for index, country in enumerate(countries, start=1):
                connection.execute("INSERT INTO trial_sites(trial_id,site_id,site_name,country,evidence_type,recruitment_status,last_verified_date,record_status) VALUES(?,?,?,?,?,?,?,'有效')",
                    (trial_id, f"COUNTRY-{index:03d}", f"{country}（仅国家记录，非已核验中心）", country, "registry_country_list", trial["recruitment_status"], trial["last_verified_date"]))
                counts["countries"] += 1
            for contact in node.findall("contacts/contact"):
                name = " ".join(filter(None, [_text(contact, "firstname"), _text(contact, "middlename"), _text(contact, "lastname")]))
                connection.execute("INSERT INTO trial_contacts(trial_id,contact_type,contact_name,affiliation,country,city,address,telephone,email) VALUES(?,?,?,?,?,?,?,?,?)",
                    (trial_id, _text(contact, "type"), name, _text(contact, "affiliation"), _text(contact, "country1"), _text(contact, "city"), _text(contact, "address"), _text(contact, "telephone"), _text(contact, "email")))
                counts["contacts"] += 1
            criteria_node = node.find("criteria")
            for kind, tag, prefix in (("inclusion", "inclusion_criteria", "INC"), ("exclusion", "exclusion_criteria", "EXC")):
                raw_text = _raw_text(criteria_node, tag)
                for order, text in enumerate(split_criteria(raw_text) or ([raw_text] if raw_text else []), start=1):
                    field, operator, value, unit = strict_structure(text, criterion_type=kind, disease=disease)
                    connection.execute("""INSERT INTO eligibility_criteria(trial_id,cohort_id,criterion_type,criterion_id,criterion_order,criterion_category,
                        criterion_text_zh,criterion_text_en,is_mandatory,structured_field,operator,structured_value,unit,normalization_note,record_status)
                        VALUES(?,'ALL',?,?,?,?,?,?,1,?,?,?,?,?,'有效')""",
                        (trial_id, kind, f"{prefix}-{order:03d}", order, category_for(text), text, text, field, operator, value, unit,
                         "XML原文；仅可靠模式进入结构化字段"))
                    counts["criteria"] += 1
            target_parts = [part.strip() for part in _text(main, "target_size").split(";") if part.strip()]
            intervention_parts = [part.strip() for part in _text(main, "i_freetext").split(";") if part.strip()]
            for index, part in enumerate(target_parts, start=1):
                name, _, size = part.partition(":")
                intervention = next((value.partition(":")[2] for value in intervention_parts if value.partition(":")[0].strip() == name.strip()), "")
                cohort_id = f"COHORT-{index:02d}"
                connection.execute("INSERT INTO trial_cohorts(trial_id,cohort_id,cohort_name,cohort_status,disease_scope,intervention_name,target_enrollment,record_status) VALUES(?,?,?,?,?,?,?,'有效')",
                    (trial_id, cohort_id, name or cohort_id, trial["recruitment_status"], name, intervention, _integer(size)))
                if intervention:
                    connection.execute("INSERT INTO trial_interventions(trial_id,cohort_id,intervention_name,description) VALUES(?,?,?,?)",
                        (trial_id, cohort_id, intervention, part))
                counts["cohorts"] += 1
            for outcome_type, path, tag in (("primary", "primary_outcome/prim_outcome", "prim_outcome"), ("secondary", "secondary_outcome/sec_outcome", "sec_outcome")):
                for order, text in enumerate([x.strip() for x in _text(node, path).split(";") if x.strip()], start=1):
                    connection.execute("INSERT INTO trial_outcomes(trial_id,outcome_type,outcome_order,outcome_text) VALUES(?,?,?,?)", (trial_id, outcome_type, order, text))
            for review in node.findall("ethics_reviews/ethics_review"):
                connection.execute("INSERT OR IGNORE INTO trial_ethics_reviews(trial_id,review_status,approval_date,committee_name,contact_name,contact_channel) VALUES(?,?,?,?,?,?)",
                    (trial_id, _text(review, "status"), _date(_text(review, "approval_date")), _text(review, "contact_address"), _text(review, "contact_name"), " | ".join(filter(None, [_text(review, "contact_phone"), _text(review, "contact_email")]))))
            for support in node.findall("source_support/source_name"):
                if clean_text(support.text):
                    connection.execute("INSERT OR IGNORE INTO trial_support_sources(trial_id,support_type,source_name) VALUES(?,'funding/support',?)", (trial_id, clean_text(support.text)))
            counts["trials"] += 1
        replace_trial_search(connection)
        connection.execute("INSERT INTO metadata(key,value) VALUES('last_xml_import',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (str(Path(xml_path).resolve()),))
    return counts


def import_mcp_snapshot(db_path: str | Path, snapshot_path: str | Path) -> dict[str, Any]:
    initialize(db_path)
    payload = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    details = payload.get("details") or []
    counts = {"trials": 0, "criteria": 0, "sites": 0, "country_records": 0, "interventions": 0}
    with connect(db_path) as connection:
        for detail in details:
            if not detail.get("found"):
                continue
            sponsor = clean_text(detail.get("sponsor_summary")) or "WHO ICTRP未填写主办单位"
            source_url = clean_text(detail.get("source_url"))
            organization_id = _ensure_organization(connection, sponsor, "非合作", source_url, _code(sponsor, "WHO"))
            registry_ids = detail.get("registry_ids") or [{"registry_source": detail.get("primary_source"), "registry_id": detail.get("primary_registry_id"), "id_type": "main_id", "is_primary": 1, "source_url": source_url}]
            primary_id = clean_text(detail.get("primary_registry_id"))
            trial = {
                "partner_trial_id": primary_id, "protocol_number": None,
                "chictr_registration_number": primary_id if primary_id.casefold().startswith("chictr") else None,
                "other_registry_ids": "|".join(clean_text(item.get("registry_id")) for item in registry_ids if not item.get("is_primary")),
                "primary_registry": clean_text(detail.get("primary_source")), "utrn": "", "title_zh": clean_text(detail.get("title")) or primary_id,
                "title_en": clean_text(detail.get("title")), "scientific_title": clean_text(detail.get("scientific_title")),
                "recruitment_status": normalize_recruitment(detail.get("recruitment_status_normalized")), "study_type": clean_text(detail.get("study_type_normalized")),
                "study_design": "", "study_phase": clean_text(detail.get("phase_normalized")), "primary_disease": clean_text(detail.get("disease_text")) or "癌症（未细分）",
                "disease_aliases": clean_text(detail.get("disease_normalized")), "histology": "", "disease_stage": "", "biomarker_summary": "",
                "intervention_summary": clean_text(detail.get("intervention_summary")), "target_enrollment": None, "target_enrollment_text": "",
                "actual_enrollment": None, "sex": "", "minimum_age_years": None, "maximum_age_years": None, "maximum_ecog": None,
                "first_enrollment_date": _date(detail.get("start_date")), "planned_end_date": _date(detail.get("completion_date")),
                "registration_date": _date(detail.get("registration_date")), "last_update_date": _date(detail.get("last_update_date")),
                "countries": clean_text(detail.get("countries")), "primary_sponsor": sponsor, "lead_institution": sponsor,
                "source_url": source_url, "last_verified_date": clean_text(detail.get("last_fetched_at"))[:10], "record_status": "有效",
                "brief_summary": clean_text(detail.get("brief_summary")), "data_notes": "WHO ICTRP MCP导入；国家记录不等于已核验中心",
            }
            trial_id = _upsert_trial(connection, organization_id, trial, registry_ids, {"source_format": "WHO_ICTRP_MCP", "detail": detail})
            for index, site in enumerate(detail.get("sites") or [], start=1):
                connection.execute("INSERT INTO trial_sites(trial_id,site_id,site_name,province,city,country,recruitment_status,principal_investigator,contact_channel,address,evidence_type,last_verified_date,record_status) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'有效')",
                    (trial_id, f"SITE-{index:03d}", clean_text(site.get("site_name")) or "未命名中心", clean_text(site.get("province")), clean_text(site.get("city")), clean_text(site.get("country")), clean_text(site.get("site_status")), clean_text(site.get("investigator")), " | ".join(filter(None, [clean_text(site.get("contact_phone")), clean_text(site.get("contact_email"))])), "", "named_site", clean_text(site.get("last_verified_at"))[:10]))
                counts["sites"] += 1
            for index, country in enumerate(detail.get("country_records") or [], start=1):
                name = clean_text(country.get("country"))
                connection.execute("INSERT INTO trial_sites(trial_id,site_id,site_name,country,evidence_type,recruitment_status,last_verified_date,record_status) VALUES(?,?,?,?,?,?,?,'有效')",
                    (trial_id, f"COUNTRY-{index:03d}", f"{name}（仅国家记录，非已核验中心）", name, clean_text(country.get("evidence_type")) or "registry_country_list", trial["recruitment_status"], clean_text(country.get("last_verified_at"))[:10]))
                counts["country_records"] += 1
            for index, intervention in enumerate(detail.get("interventions") or [], start=1):
                name = clean_text(intervention.get("intervention_name_raw"))
                if not name:
                    continue
                connection.execute("INSERT INTO trial_interventions(trial_id,cohort_id,intervention_name,intervention_type,target,mechanism,therapy_class) VALUES(?,'ALL',?,?,?,?,?)",
                    (trial_id, name, clean_text(intervention.get("intervention_type")), clean_text(intervention.get("target")), clean_text(intervention.get("mechanism")), clean_text(intervention.get("therapy_class"))))
                counts["interventions"] += 1
            for index, item in enumerate(detail.get("criteria") or [], start=1):
                kind = clean_text(item.get("criterion_type"))
                if kind not in {"inclusion", "exclusion"}:
                    continue
                text = clean_text(item.get("criterion_text"))
                if not text:
                    continue
                field, operator, value, unit = strict_structure(text, criterion_type=kind, disease=trial["primary_disease"])
                connection.execute("""INSERT INTO eligibility_criteria(trial_id,cohort_id,criterion_type,criterion_id,criterion_order,criterion_category,criterion_text_zh,criterion_text_en,
                    is_mandatory,structured_field,operator,structured_value,unit,normalization_note,record_status) VALUES(?,'ALL',?,?,?,?,?,?,1,?,?,?,?,?,'有效')""",
                    (trial_id, kind, f"{kind[:3].upper()}-{index:03d}", int(item.get("criterion_order") or index), clean_text(item.get("parsed_category")) or category_for(text), text,
                     text if clean_text(item.get("language")) == "en" else None, field, operator, value, unit, "MCP注册库原文；断行可能不是独立医学条件"))
                counts["criteria"] += 1
            counts["trials"] += 1
        tokenizer = replace_trial_search(connection)
        for key, value in {"mcp_database_as_of": payload.get("metadata", {}).get("database_as_of"), "mcp_snapshot_file": str(Path(snapshot_path).resolve()), "mcp_trial_count": counts["trials"]}.items():
            connection.execute("INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value or "")))
    return {**counts, "fts_tokenizer": tokenizer, "database_as_of": payload.get("metadata", {}).get("database_as_of")}
