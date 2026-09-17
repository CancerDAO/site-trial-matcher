from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .database import connect, initialize
from .demo_builder import build_demo
from .pipeline import match_batch
from .model_screening import execute_model_jobs, merge_model_results, prepare_model_jobs
from .registry_importer import import_chictr_xml, import_mcp_snapshot
from .who_mcp_client import fetch_recent_china_trials, hydrate_snapshot


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="china-trial-demo")
    commands = root.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init-db")
    init.add_argument("--db", type=Path, required=True)
    demo = commands.add_parser("build-demo")
    demo.add_argument("--source-db", type=Path, required=True)
    demo.add_argument("--db", type=Path, required=True)
    demo.add_argument("--top30", type=Path, default=PROJECT_ROOT / "data" / "chictr_top30_institutions.json")
    imp = commands.add_parser("import-workbook")
    imp.add_argument("--db", type=Path, required=True)
    imp.add_argument("--workbook", type=Path, required=True)
    xml = commands.add_parser("import-xml")
    xml.add_argument("--db", type=Path, required=True); xml.add_argument("--xml", type=Path, required=True)
    xml.add_argument("--organization-code"); xml.add_argument("--organization-name"); xml.add_argument("--partner-status", default="合作")
    fetch = commands.add_parser("fetch-mcp")
    fetch.add_argument("--out", type=Path, required=True); fetch.add_argument("--limit", type=int, default=300); fetch.add_argument("--workers", type=int, default=16); fetch.add_argument("--scan-limit", type=int, default=5000)
    fetch.add_argument("--allow-partial", action="store_true", help="仅诊断使用；允许分页不完整的快照")
    hydrate = commands.add_parser("hydrate-mcp-snapshot")
    hydrate.add_argument("--snapshot", type=Path, required=True); hydrate.add_argument("--workers", type=int, default=16)
    import_mcp = commands.add_parser("import-mcp")
    import_mcp.add_argument("--db", type=Path, required=True); import_mcp.add_argument("--snapshot", type=Path, required=True)
    match = commands.add_parser("match-batch")
    match.add_argument("--db", type=Path, required=True)
    match.add_argument("--patients", type=Path, required=True)
    match.add_argument("--out", type=Path, required=True)
    match.add_argument("--candidate-limit", type=int, default=0, help="0 表示不截断；正整数仅用于调试")
    match.add_argument("--aliases", type=Path, default=PROJECT_ROOT / "data" / "disease_ontology.json")
    prepare_model = commands.add_parser("prepare-model-jobs")
    prepare_model.add_argument("--db", type=Path, required=True)
    prepare_model.add_argument("--patients", type=Path, required=True)
    prepare_model.add_argument("--deterministic-results", type=Path, required=True)
    prepare_model.add_argument("--run-dir", type=Path, required=True)
    prepare_model.add_argument("--skill", type=Path, default=PROJECT_ROOT / "skills" / "china-trial-exclusion-gater" / "SKILL.md")
    prepare_model.add_argument("--scope-batch-size", type=int, default=8)
    prepare_model.add_argument("--eligibility-batch-size", type=int, default=2)
    run_model = commands.add_parser("run-model-jobs")
    run_model.add_argument("--run-dir", type=Path, required=True)
    run_model.add_argument("--workers", type=int, default=8)
    merge_model = commands.add_parser("merge-model-results")
    merge_model.add_argument("--run-dir", type=Path, required=True)
    merge_model.add_argument("--out", type=Path, required=True)
    organizations = commands.add_parser("list-organizations")
    organizations.add_argument("--db", type=Path, required=True)
    return root


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parser().parse_args()
    if args.command == "init-db":
        initialize(args.db)
        result = {"db": str(args.db.resolve()), "initialized": True}
    elif args.command == "build-demo":
        result = build_demo(args.source_db, args.db, args.top30)
    elif args.command == "import-workbook":
        from .importer import import_workbook
        result = import_workbook(args.db, args.workbook)
    elif args.command == "import-xml":
        result = import_chictr_xml(args.db, args.xml, organization_code=args.organization_code, organization_name=args.organization_name, partner_status=args.partner_status)
    elif args.command == "fetch-mcp":
        result = fetch_recent_china_trials(args.out, limit=args.limit, workers=args.workers, scan_limit=args.scan_limit,
                                           require_complete=not args.allow_partial)
    elif args.command == "hydrate-mcp-snapshot":
        result = hydrate_snapshot(args.snapshot, workers=args.workers)
    elif args.command == "import-mcp":
        result = import_mcp_snapshot(args.db, args.snapshot)
    elif args.command == "match-batch":
        result = match_batch(args.db, args.patients, args.out, aliases_path=args.aliases, candidate_limit=args.candidate_limit or None)
    elif args.command == "prepare-model-jobs":
        result = prepare_model_jobs(args.db, args.patients, args.deterministic_results, args.run_dir, args.skill,
                                    scope_batch_size=args.scope_batch_size, eligibility_batch_size=args.eligibility_batch_size)
    elif args.command == "run-model-jobs":
        result = execute_model_jobs(args.run_dir, workers=args.workers)
    elif args.command == "merge-model-results":
        result = merge_model_results(args.run_dir, args.out)
    else:
        with connect(args.db, readonly=True) as connection:
            result = [dict(row) for row in connection.execute("SELECT organization_code,organization_name_zh,partner_status,official_rank,official_trial_count FROM organizations ORDER BY coalesce(official_rank,999999),organization_code")]
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
