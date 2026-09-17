from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable

from .database import connect
from .importer import import_workbook
from .registry_importer import import_chictr_xml, import_mcp_snapshot
from .who_mcp_client import fetch_recent_china_trials


def validate_database(path: str | Path, *, min_trials: int = 1) -> dict[str, Any]:
    with connect(path, readonly=True) as connection:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_key_errors = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        trials = int(connection.execute("SELECT COUNT(*) FROM trials").fetchone()[0])
        criteria = int(connection.execute("SELECT COUNT(*) FROM eligibility_criteria").fetchone()[0])
        recruiting = int(connection.execute(
            "SELECT COUNT(*) FROM trials WHERE recruitment_status IN ('recruiting','招募中')"
        ).fetchone()[0])
        metadata = {
            str(row[0]): str(row[1])
            for row in connection.execute("SELECT key,value FROM metadata")
        }
    if integrity != "ok":
        raise ValueError(f"SQLite integrity_check failed: {integrity}")
    if foreign_key_errors:
        raise ValueError(f"SQLite foreign_key_check failed: {foreign_key_errors} rows")
    if trials < min_trials:
        raise ValueError(f"trial count {trials} is below required minimum {min_trials}")
    if criteria < 1:
        raise ValueError("database contains no eligibility criteria")
    if recruiting < 1:
        raise ValueError("database contains no recruiting trials")
    return {
        "integrity": integrity,
        "foreign_key_errors": foreign_key_errors,
        "trials": trials,
        "criteria": criteria,
        "recruiting_trials": recruiting,
        "database_as_of": metadata.get("mcp_database_as_of", ""),
    }


def refresh_database(
    db_path: str | Path,
    snapshot_path: str | Path,
    *,
    fetch: bool = True,
    limit: int = 300,
    workers: int = 16,
    scan_limit: int = 5000,
    allow_partial: bool = False,
    min_trials: int = 1,
    workbooks: Iterable[str | Path] = (),
    xml_files: Iterable[str | Path] = (),
) -> dict[str, Any]:
    """Build, validate, and atomically replace a production trial database."""
    target = Path(db_path).resolve()
    snapshot = Path(snapshot_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    workbook_paths = [Path(item).resolve() for item in workbooks]
    xml_paths = [Path(item).resolve() for item in xml_files]
    for source in [*workbook_paths, *xml_paths]:
        if not source.is_file():
            raise FileNotFoundError(source)

    with tempfile.TemporaryDirectory(prefix=".trial-db-refresh-", dir=target.parent) as db_directory, \
            tempfile.TemporaryDirectory(prefix=".trial-snapshot-refresh-", dir=snapshot.parent) as snapshot_directory:
        staged_db = Path(db_directory) / "trials.db"
        staged_snapshot = Path(snapshot_directory) / "snapshot.json"
        if fetch:
            fetch_result = fetch_recent_china_trials(
                staged_snapshot,
                limit=limit,
                workers=workers,
                scan_limit=scan_limit,
                require_complete=not allow_partial,
            )
        else:
            if not snapshot.is_file():
                raise FileNotFoundError(snapshot)
            shutil.copy2(snapshot, staged_snapshot)
            fetch_result = {"output": str(snapshot), "reused_snapshot": True}

        mcp_result = import_mcp_snapshot(staged_db, staged_snapshot)
        workbook_results = [import_workbook(staged_db, item) for item in workbook_paths]
        xml_results = [import_chictr_xml(staged_db, item) for item in xml_paths]
        with connect(staged_db) as connection:
            connection.execute(
                "INSERT INTO metadata(key,value) VALUES('mcp_snapshot_file',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(snapshot),),
            )
        validation = validate_database(staged_db, min_trials=min_trials)

        if fetch:
            os.replace(staged_snapshot, snapshot)
        os.replace(staged_db, target)

    return {
        "database": str(target),
        "snapshot": str(snapshot),
        "fetch": fetch_result,
        "mcp_import": mcp_result,
        "workbook_imports": workbook_results,
        "xml_imports": xml_results,
        "validation": validation,
    }
