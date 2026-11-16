from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class Storage:
    """Tiny SQLite store so re-runs never apply to the same job twice."""

    def __init__(self, db_path: Path):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id      TEXT PRIMARY KEY,
                title       TEXT,
                company     TEXT,
                salary      TEXT,
                location    TEXT,
                url         TEXT,
                score       REAL DEFAULT 0,
                status      TEXT NOT NULL DEFAULT 'discovered',
                error       TEXT,
                created_at  TEXT,
                updated_at  TEXT,
                applied_at  TEXT,
                resumed_at  TEXT,
                chatted_at  TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
            """
        )
        # Safe migration for databases created before resumed_at existed.
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(jobs)")}
        if "resumed_at" not in columns:
            self.conn.execute("ALTER TABLE jobs ADD COLUMN resumed_at TEXT")
        self.conn.commit()

    def known_job_ids(self) -> set[str]:
        return {row[0] for row in self.conn.execute("SELECT job_id FROM jobs")}

    def known_jobs(self) -> dict[str, str]:
        return {
            row["job_id"]: row["status"]
            for row in self.conn.execute("SELECT job_id, status FROM jobs")
        }

    def save_jobs(self, jobs: list[dict]) -> None:
        now = _now()
        for job in jobs:
            self.conn.execute(
                """INSERT OR IGNORE INTO jobs
                   (job_id, title, company, salary, location, url, score, status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'discovered', ?, ?)""",
                (
                    job.get("job_id"),
                    job.get("title"),
                    job.get("company"),
                    job.get("salary"),
                    job.get("location"),
                    job.get("url"),
                    job.get("score", 0),
                    now,
                    now,
                ),
            )
        self.conn.commit()

    def update(self, job_id: str, **fields) -> None:
        fields["updated_at"] = _now()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        self.conn.execute(
            f"UPDATE jobs SET {assignments} WHERE job_id = ?",
            (*fields.values(), job_id),
        )
        self.conn.commit()

    def status_counts(self) -> dict[str, int]:
        return {
            row["status"]: row["n"]
            for row in self.conn.execute(
                "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
            )
        }

    def applied_today_count(self) -> int:
        today = datetime.now().date().isoformat()
        return self.conn.execute(
            """SELECT COUNT(*) FROM jobs
               WHERE status IN ('applied', 'chatted') AND date(applied_at) = ?""",
            (today,),
        ).fetchone()[0]

    def close(self) -> None:
        self.conn.close()
