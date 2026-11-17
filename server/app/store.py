"""Small, transactional SQLite store for Instagram relationship snapshots."""

from __future__ import annotations

from contextlib import closing, contextmanager
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Iterator

from igviewer.data import Record, dataset_name_for_path, relationship_metrics
from igviewer.locking import file_lock


class TrendStore:
    def __init__(self, database_path: Path):
        self.database_path = database_path
        self.lock_path = database_path.with_suffix(".ingest.lock")

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with file_lock(self.lock_path), self.connection() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                PRAGMA foreign_keys = ON;
                CREATE TABLE IF NOT EXISTS snapshots (
                    id INTEGER PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
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
                """
            )
            indexes = connection.execute("PRAGMA index_list(snapshots)").fetchall()
            fingerprint_is_unique = False
            for index in indexes:
                columns = [row["name"] for row in connection.execute("SELECT name FROM pragma_index_info(?)", (index["name"],))]
                if index["unique"] and columns == ["fingerprint"]:
                    fingerprint_is_unique = True
            if fingerprint_is_unique:
                # Preserve IDs and memberships from databases created before v2.
                connection.execute("PRAGMA foreign_keys = OFF")
                connection.execute("BEGIN IMMEDIATE")
                connection.execute("""CREATE TABLE snapshots_v2 (
                    id INTEGER PRIMARY KEY, fingerprint TEXT NOT NULL,
                    imported_at TEXT NOT NULL, source_kind TEXT NOT NULL,
                    source_label TEXT NOT NULL, archive_path TEXT NOT NULL,
                    metrics_json TEXT NOT NULL
                )""")
                connection.execute("INSERT INTO snapshots_v2 SELECT * FROM snapshots")
                connection.execute("DROP TABLE snapshots")
                connection.execute("ALTER TABLE snapshots_v2 RENAME TO snapshots")
                for row in connection.execute("SELECT id, archive_path FROM snapshots").fetchall():
                    try:
                        manifest = json.loads((Path(row["archive_path"]) / "manifest.json").read_text(encoding="utf-8"))
                        if not isinstance(manifest, list) or any(
                            not isinstance(item, dict)
                            or not isinstance(item.get("source"), str)
                            or not isinstance(item.get("sha256"), str)
                            for item in manifest
                        ):
                            continue
                        identities = sorted(
                            (item["source"].casefold(), item["sha256"])
                            for item in manifest if dataset_name_for_path(item["source"]) is not None
                        )
                        if identities:
                            fingerprint = sha256(json.dumps(identities, separators=(",", ":")).encode("utf-8")).hexdigest()
                            connection.execute("UPDATE snapshots SET fingerprint = ? WHERE id = ?", (fingerprint, row["id"]))
                    except (OSError, UnicodeError, TypeError, ValueError, KeyError):
                        # Archives may already have expired under the retention policy.
                        continue
                connection.commit()
                connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("CREATE INDEX IF NOT EXISTS snapshots_fingerprint ON snapshots(fingerprint)")
            connection.execute("DROP INDEX IF EXISTS memberships_lookup")
            connection.execute("PRAGMA user_version = 2")

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

    def accounts_for(self, snapshot_id: int, view: str, query: str, limit: int, offset: int = 0) -> list[str]:
        relationship, other, match = {
            "mutual": ("following", "followers", "EXISTS"),
            "not_following_back": ("following", "followers", "NOT EXISTS"),
            "not_followed_back": ("followers", "following", "NOT EXISTS"),
        }[view]
        with self.connection() as connection:
            rows = connection.execute(
                f"""SELECT current.username FROM memberships AS current
                    WHERE current.snapshot_id = ? AND current.relationship = ?
                      AND {match} (SELECT 1 FROM memberships AS other
                          WHERE other.snapshot_id = current.snapshot_id
                            AND other.relationship = ? AND other.username = current.username)
                      AND instr(current.username, ?) > 0
                    ORDER BY current.username LIMIT ? OFFSET ?""",
                (snapshot_id, relationship, other, query.casefold().strip(), limit, offset),
            ).fetchall()
        return [row["username"] for row in rows]

    def archived_snapshot(self, fingerprint: str) -> dict | None:
        with self.connection() as connection:
            rows = connection.execute("SELECT * FROM snapshots WHERE fingerprint = ? ORDER BY id DESC", (fingerprint,))
            for row in rows:
                if Path(row["archive_path"]).is_dir():
                    return self._snapshot_from_row(row)
        return None

    def backup(self, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as source, closing(sqlite3.connect(target)) as destination:
            source.backup(destination)

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
        """Record a changed source state and its delta from the previous one."""
        metrics = relationship_metrics(datasets)
        now = datetime.now(timezone.utc).isoformat()
        memberships = {
            "followers": {record[0].casefold() for record in datasets.get("粉絲", [])},
            "following": {record[0].casefold() for record in datasets.get("追蹤中", [])},
        }
        with self.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM snapshots ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if existing and existing["fingerprint"] == fingerprint:
                return self._snapshot_from_row(existing), False, None
            previous_row = existing
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
