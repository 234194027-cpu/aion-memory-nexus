"""Validate the full Alembic chain against an isolated temporary SQLite DB."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _run_alembic(arguments: list[str], env: dict[str, str]) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", *arguments],
        cwd=PROJECT_ROOT,
        env=env,
        check=True,
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="life-memory-migrations-") as temp_dir:
        database_path = Path(temp_dir) / "migration-check.db"
        env = os.environ.copy()
        env["POSTGRES_URL"] = f"sqlite+aiosqlite:///{database_path.as_posix()}"
        env["TESTING"] = "true"

        _run_alembic(["upgrade", "head"], env)
        _run_alembic(["downgrade", "-1"], env)
        _run_alembic(["upgrade", "head"], env)

    print("Migration chain validated on an isolated SQLite database.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
