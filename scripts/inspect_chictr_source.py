from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("db", type=Path)
    args = parser.parse_args()
    connection = sqlite3.connect(f"file:{args.db.resolve().as_posix()}?mode=ro", uri=True)
    tables = connection.execute(
        "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    report: dict[str, object] = {"db": str(args.db.resolve()), "tables": []}
    for name, sql in tables:
        columns = [row[1] for row in connection.execute(f'PRAGMA table_info("{name}")')]
        count = connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
        report["tables"].append({"name": name, "count": count, "columns": columns, "sql": sql})
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

