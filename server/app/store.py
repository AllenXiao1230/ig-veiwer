"""Small, transactional SQLite store for Instagram relationship snapshots."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Iterator

from igviewer.data import Record, relationship_metrics


class TrendStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY,
                    fingerprint TEXT NOT NULL UNIQUE,
                    imported_at TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    source_label TEXT NOT NULL,
                    archive_path TEXT NOT NULL,
                    metrics_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memberships (
                    snapshot_id INTEGER NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
                    relationship TEXT NOT NULL,
                    username TEXT NOT NULL,
                    PRIMARY KEY (snapshot_id, relationship, username)
                );
                CREATE INDEX IF NOT EXISTS memberships_lookup
                    ON memberships(snapshot_id, relationship, username);
                """
            )

    def latest_snapshot(self) -> dict | None:
        with self.connection() as connection:
            row = connection.execute(
                "SELECT * FROM snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return self._snapshot_from_row(row) if row else None

    def snapshot_history(self, limit: int = 90) -> list[dict]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT * FROM snapshots ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._snapshot_from_row(row) for row in rows]

    def memberships_for(self, snapshot_id: int, relationship: str) -> set[str]:
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT username FROM memberships WHERE snapshot_id = ? AND relationship = ?",
                (snapshot_id, relationship),
            ).fetchall()
        return {row["username"] for row in rows}

    @staticmethod
    def _snapshot_from_row(row: sqlite3.Row) -> dict:
        return {
            "id": row["id"],
            "fingerprint": row["fingerprint"],
            "imported_at": row["imported_at"],
            "source_kind": row["source_kind"],
            "source_label": row["source_label"],
            "archive_path": row["archive_path"],
            "metrics": json.loads(row["metrics_json"]),
        }

    def add_snapshot(
        self,
        fingerprint: str,
        datasets: dict[str, list[Record]],
        source_kind: str,
        source_label: str,
        archive_path: Path,
    ) -> tuple[dict, bool, dict | None]:
        """Add one unique snapshot and return its delta from the previous one."""
        metrics = relationship_metrics(datasets)
        now = datetime.now(timezone.utc).isoformat()
        memberships = {
            "followers": {record[0].casefold() for record in datasets.get("粉絲", [])},
            "following": {record[0].casefold() for record in datasets.get("追蹤中", [])},
        }
        with self.connection() as connection:
            existing = connection.execute(
                "SELECT * FROM snapshots WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
            if existing:
                return self._snapshot_from_row(existing), False, None
            previous_row = connection.execute(
                "SELECT * FROM snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
            cursor = connection.execute(
                """INSERT INTO snapshots
                   (fingerprint, imported_at, source_kind, source_label, archive_path, metrics_json)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (fingerprint, now, source_kind, source_label, str(archive_path), json.dumps(metrics)),
            )
            snapshot_id = cursor.lastrowid
            for relationship, usernames in memberships.items():
                connection.executemany(
                    "INSERT INTO memberships (snapshot_id, relationship, username) VALUES (?, ?, ?)",
                    [(snapshot_id, relationship, username) for username in sorted(usernames)],
                )
            snapshot = self._snapshot_from_row(
                connection.execute("SELECT * FROM snapshots WHERE id = ?", (snapshot_id,)).fetchone()
            )
            if previous_row is None:
                return snapshot, True, None
            previous_id = previous_row["id"]
            previous_memberships = {
                relationship: {
                    row["username"]
                    for row in connection.execute(
                        "SELECT username FROM memberships WHERE snapshot_id = ? AND relationship = ?",
                        (previous_id, relationship),
                    )
                }
                for relationship in memberships
            }
            delta = {
                "new_followers": len(memberships["followers"] - previous_memberships["followers"]),
                "lost_followers": len(previous_memberships["followers"] - memberships["followers"]),
                "new_following": len(memberships["following"] - previous_memberships["following"]),
                "unfollowed": len(previous_memberships["following"] - memberships["following"]),
            }
            return snapshot, True, delta
