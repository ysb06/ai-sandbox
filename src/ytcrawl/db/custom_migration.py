"""Allow imported videos without inventing a YouTube search run."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
import sqlite3
from uuid import uuid4


def ensure_optional_search_id(db_path: Path, backup_root: Path) -> Path | None:
    """Rebuild legacy SQLite videos atomically; preserve IDs and dependent rows."""
    if not db_path.is_file():
        return None
    connection = sqlite3.connect(db_path, isolation_level=None, timeout=30)
    backup_path: Path | None = None
    try:
        columns = connection.execute('PRAGMA table_info("videos")').fetchall()
        if not any(row[1] == "search_id" and row[3] for row in columns):
            return None
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute("PRAGMA legacy_alter_table=ON")
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Existing foreign-key violations; migration aborted.")
        original_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='videos'"
        ).fetchone()[0]
        # Change only this column's nullability; retain any other columns/constraints.
        sql, count = re.subn(
            r'(?im)([\n,(]\s*["`\[]?search_id["`\]]?\s+[^,\n]*?)\bNOT\s+NULL\b',
            r'\1', original_sql,
        )
        if count != 1:
            raise RuntimeError("Unsupported videos DDL; migration aborted.")
        table_name = "videos_custom_" + uuid4().hex
        sql, count = re.subn(
            r'(?i)^(CREATE\s+TABLE\s+)(?:"videos"|`videos`|\[videos\]|videos)(?=\s*\()',
            lambda match: match.group(1) + '"' + table_name + '"', sql, count=1,
        )
        if count != 1:
            raise RuntimeError("Unsupported videos table declaration.")
        objects = connection.execute(
            "SELECT sql FROM sqlite_master WHERE tbl_name='videos' "
            "AND type IN ('index','trigger') AND sql IS NOT NULL"
        ).fetchall()
        sequence = None
        if connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='sqlite_sequence'"
        ).fetchone():
            sequence = connection.execute(
                "SELECT seq FROM sqlite_sequence WHERE name='videos'"
            ).fetchone()

        backup_root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = backup_root / f"ytcrawl-before-custom-{stamp}-{uuid4().hex}.sqlite3"
        # Separate reader sees the snapshot locked against writers by BEGIN IMMEDIATE.
        with sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True) as source:
            with sqlite3.connect(backup_path) as target:
                source.backup(target)
                if target.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise RuntimeError("Database backup verification failed.")
        names = ", ".join('"' + row[1].replace('"', '""') + '"' for row in columns)
        before = connection.execute('SELECT COUNT(*) FROM "videos"').fetchone()[0]
        connection.execute(sql)
        connection.execute(
            f'INSERT INTO "{table_name}" ({names}) SELECT {names} FROM "videos"'
        )
        connection.execute('DROP TABLE "videos"')
        connection.execute(f'ALTER TABLE "{table_name}" RENAME TO "videos"')
        for (object_sql,) in objects:
            connection.execute(object_sql)
        if sequence is not None:
            connection.execute("DELETE FROM sqlite_sequence WHERE name='videos'")
            connection.execute(
                "INSERT INTO sqlite_sequence(name, seq) VALUES ('videos', ?)", sequence
            )
        after = connection.execute('SELECT COUNT(*) FROM "videos"').fetchone()[0]
        if before != after or connection.execute("PRAGMA foreign_key_check").fetchall():
            raise RuntimeError("Migration row-count/foreign-key verification failed.")
        if connection.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise RuntimeError("Migration integrity verification failed.")
        connection.execute("COMMIT")
        return backup_path
    except Exception as exc:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise RuntimeError(
            f"Search-ID migration failed; no downloads started. Backup: {backup_path}. {exc}"
        ) from exc
    finally:
        connection.close()
