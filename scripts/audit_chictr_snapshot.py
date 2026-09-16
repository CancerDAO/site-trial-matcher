from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any


CANCER_MARKERS = ("癌", "肿瘤", "白血病", "淋巴瘤", "骨髓瘤", "肉瘤", "胶质瘤", "母细胞瘤", "carcinoma", "cancer", "tumor", "tumour", "neoplasm", "leukemia", "lymphoma", "sarcoma", "melanoma", "myeloma")


def normalized(value: Any) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").casefold())


def has_cancer_text(*values: Any) -> bool:
    text = " ".join(str(value or "") for value in values).casefold()
    return any(marker.casefold() in text for marker in CANCER_MARKERS)


def detail_is_cancer(detail: dict[str, Any]) -> bool:
    return has_cancer_text(*(detail.get(key) for key in ("disease", "disease_en", "title", "title_en", "objectives", "objectives_en")))


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit the local ChiCTR snapshot selection funnel.")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--top30", type=Path, required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    ranking = json.loads(args.top30.read_text(encoding="utf-8"))
    top_items = ranking["institutions"]
    exact_names = {item["name_zh"] for item in top_items}
    normalized_names = {normalized(item["name_zh"]): item["name_zh"] for item in top_items}

    connection = sqlite3.connect(f"file:{args.db.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    rows = [dict(row) for row in connection.execute("SELECT * FROM trials ORDER BY registration_number")]
    scrape_state = {row["key"]: row["value"] for row in connection.execute("SELECT key,value FROM scrape_state ORDER BY key")}
    crawl_events = [dict(row) for row in connection.execute("SELECT event_time,event_type,url,request_count,message FROM crawl_events ORDER BY id")]
    connection.close()

    parse_errors = 0
    exact_top_index = 0
    normalized_top_index = 0
    exact_top_details = 0
    normalized_top_details = 0
    exact_top_cancer_details = 0
    exact_top_cancer_index_titles = 0
    all_cancer_details = 0
    institution_counts = Counter()
    per_top = {name: {"index_rows": 0, "detail_rows": 0, "cancer_detail_rows": 0, "cancer_index_title_rows": 0} for name in exact_names}
    detail_missing_by_top = Counter()
    normalized_only_examples: list[dict[str, str]] = []

    for row in rows:
        institution = str(row.get("institution") or "").strip()
        institution_counts[institution] += 1
        exact = institution if institution in exact_names else None
        normalized_match = normalized_names.get(normalized(institution))
        if exact:
            exact_top_index += 1; per_top[exact]["index_rows"] += 1
            if has_cancer_text(row.get("title"), row.get("study_type")):
                exact_top_cancer_index_titles += 1; per_top[exact]["cancer_index_title_rows"] += 1
        if normalized_match:
            normalized_top_index += 1
            if not exact and len(normalized_only_examples) < 20:
                normalized_only_examples.append({"source": institution, "top30": normalized_match})
        raw = row.get("detail_json")
        if not raw:
            if exact: detail_missing_by_top[exact] += 1
            continue
        try: detail = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            parse_errors += 1; continue
        cancer_detail = detail_is_cancer(detail)
        if cancer_detail: all_cancer_details += 1
        if exact:
            exact_top_details += 1; per_top[exact]["detail_rows"] += 1
            if cancer_detail:
                exact_top_cancer_details += 1; per_top[exact]["cancer_detail_rows"] += 1
        if normalized_match: normalized_top_details += 1

    rank_rows = []
    by_name = {item["name_zh"]: item for item in top_items}
    for name, counts in sorted(per_top.items(), key=lambda item: by_name[item[0]]["rank"]):
        rank_rows.append({"rank": by_name[name]["rank"], "institution": name, "official_total": by_name[name]["trial_count"], **counts,
                          "detail_coverage": round(counts["detail_rows"] / counts["index_rows"], 4) if counts["index_rows"] else 0})

    report = {
        "snapshot": {"index_rows": len(rows), "detail_fetched_flag": sum(bool(row.get("detail_fetched")) for row in rows),
                     "detail_json_rows": sum(bool(row.get("detail_json")) for row in rows), "detail_parse_errors": parse_errors,
                     "distinct_index_institutions": len(institution_counts),
                     "registration_date_min": min((str(row.get("registration_date")) for row in rows if row.get("registration_date")), default=None),
                     "registration_date_max": max((str(row.get("registration_date")) for row in rows if row.get("registration_date")), default=None),
                     "discovered_year_counts": dict(sorted(Counter(str(row.get("discovered_year")) for row in rows).items()))},
        "scrape_state": scrape_state,
        "crawl_events": crawl_events,
        "selection_funnel": {"top30_exact_index_rows": exact_top_index, "top30_normalized_index_rows": normalized_top_index,
            "top30_exact_detail_rows": exact_top_details, "top30_normalized_detail_rows": normalized_top_details,
            "all_cancer_detail_rows": all_cancer_details, "top30_exact_cancer_index_title_rows": exact_top_cancer_index_titles,
            "top30_exact_cancer_detail_rows": exact_top_cancer_details,
            "non_top30_cancer_detail_rows": all_cancer_details - exact_top_cancer_details},
        "coverage": {"all_snapshot_detail_coverage": round(sum(bool(row.get("detail_json")) for row in rows) / len(rows), 4) if rows else 0,
            "top30_detail_coverage": round(exact_top_details / exact_top_index, 4) if exact_top_index else 0},
        "top30_breakdown": rank_rows,
        "top30_missing_details": dict(detail_missing_by_top.most_common()),
        "normalized_only_examples": normalized_only_examples,
        "largest_snapshot_institutions": [{"institution": name, "rows": count, "is_top30_exact": name in exact_names} for name, count in institution_counts.most_common(40)],
        "conclusion": "This snapshot is a capped sample of index records and only partially fetched details; it cannot estimate all trials owned by the official Top30 institutions.",
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True); args.out.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
