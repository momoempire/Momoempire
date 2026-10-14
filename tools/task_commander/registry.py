"""Empire Task Commander: provider-neutral, persistent task ownership.

Standalone standard-library implementation; no production routes are changed.
"""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class TaskRegistry:
    def __init__(self, database_path):
        self.path = str(Path(database_path))
        self._init()

    def _connect(self):
        conn = sqlite3.connect(self.path, timeout=15, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    def _init(self):
        with self._connect() as con:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("""CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                project TEXT NOT NULL,
                dedupe_key TEXT NOT NULL,
                title TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                  CHECK(status IN ('pending','claimed','blocked','review','done')),
                owner TEXT,
                evidence TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(project, dedupe_key)
            )""")

    def add(self, project, dedupe_key, title):
        if not all((project.strip(), dedupe_key.strip(), title.strip())):
            raise ValueError("Project, dedupe_key, and title are required")
        task_id = str(uuid.uuid4())
        now = utcnow()
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "INSERT OR IGNORE INTO tasks(id, project, dedupe_key, title, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                (task_id, project, dedupe_key, title, now, now))
            row = con.execute(
                "SELECT * FROM tasks WHERE project=? AND dedupe_key=?",
                (project, dedupe_key)).fetchone()
            con.commit()
        return dict(row)

    def claim(self, task_id, agent):
        if not agent.strip():
            raise ValueError("Agent is required")
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            count = con.execute(
                "UPDATE tasks SET status='claimed', owner=?, updated_at=? WHERE id=? AND status='pending' AND owner IS NULL",
                (agent, utcnow(), task_id)).rowcount
            con.commit()
        return count == 1

    def submit_for_review(self, task_id, agent, evidence):
        if not evidence.strip():
            raise ValueError("Completion evidence is required")
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            count = con.execute(
                "UPDATE tasks SET status='review', evidence=?, updated_at=? WHERE id=? AND owner=? AND status='claimed'",
                (evidence, utcnow(), task_id, agent)).rowcount
            con.commit()
        return count == 1

    def verify_completion(self, task_id, reviewer):
        if not reviewer.strip():
            raise ValueError("Reviewer is required")
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT owner, evidence FROM tasks WHERE id=? AND status='review'",
                (task_id,)).fetchone()
            if not row or not row["evidence"] or row["owner"] == reviewer:
                con.rollback()
                return False
            count = con.execute(
                "UPDATE tasks SET status='done', updated_at=? WHERE id=? AND status='review'",
                (utcnow(), task_id)).rowcount
            con.commit()
        return count == 1

    def list(self, status=None):
        with self._connect() as con:
            if status is None:
                rows = con.execute("SELECT * FROM tasks ORDER BY created_at, id").fetchall()
            else:
                rows = con.execute("SELECT * FROM tasks WHERE status=? ORDER BY created_at, id", (status,)).fetchall()
        return [dict(r) for r in rows]


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Persistent task registry (offline foundation)")
    parser.add_argument("database", help="SQLite file location")
    sub = parser.add_subparsers(dest="action", required=True)
    add = sub.add_parser("add")
    for name in ("project", "dedupe_key", "title"):
        add.add_argument(name)
    claim = sub.add_parser("claim")
    claim.add_argument("task_id")
    claim.add_argument("agent")
    review = sub.add_parser("review")
    for name in ("task_id", "agent", "evidence"):
        review.add_argument(name)
    verify = sub.add_parser("verify")
    verify.add_argument("task_id")
    verify.add_argument("reviewer")
    show = sub.add_parser("list")
    show.add_argument("--status")
    args = parser.parse_args()
    registry = TaskRegistry(args.database)
    if args.action == "add":
        result = registry.add(args.project, args.dedupe_key, args.title)
    elif args.action == "claim":
        result = {"claimed": registry.claim(args.task_id, args.agent)}
    elif args.action == "review":
        result = {"submitted": registry.submit_for_review(args.task_id, args.agent, args.evidence)}
    elif args.action == "verify":
        result = {"verified": registry.verify_completion(args.task_id, args.reviewer)}
    else:
        result = registry.list(args.status)
    print(json.dumps(result, indent=2))
