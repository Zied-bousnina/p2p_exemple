"""
CacheDB — SQLite local index of files and slice locations.
Keeps track of: which files the user owns, and which peer holds each slice.
"""
import sqlite3
from pathlib import Path
from typing import List, Optional


DDL = """
CREATE TABLE IF NOT EXISTS files (
    file_id      TEXT PRIMARY KEY,
    filename     TEXT NOT NULL,
    total_blocks INTEGER NOT NULL,
    owner_id     TEXT NOT NULL,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS slices (
    file_id      TEXT NOT NULL,
    block_index  INTEGER NOT NULL,
    peer_node_id TEXT NOT NULL,
    PRIMARY KEY (file_id, block_index),
    FOREIGN KEY (file_id) REFERENCES files(file_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_files_owner ON files(owner_id);
CREATE INDEX IF NOT EXISTS idx_slices_file ON slices(file_id);
"""


class CacheDB:
    def __init__(self, db_path: str = "/data/cache.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(DDL)
        self.conn.commit()

    # ── write ─────────────────────────────────────────────────
    def upsert_file(self, file_id: str, filename: str,
                    total_blocks: int, owner_id: str) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO files
               (file_id, filename, total_blocks, owner_id)
               VALUES (?,?,?,?)""",
            (file_id, filename, total_blocks, owner_id),
        )
        self.conn.commit()

    def upsert_slice(self, file_id: str, block_index: int,
                     peer_node_id: str) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO slices
               (file_id, block_index, peer_node_id) VALUES (?,?,?)""",
            (file_id, block_index, peer_node_id),
        )
        self.conn.commit()

    def rebuild_from_metas(self, metas: List[dict], self_node_id: str) -> None:
        """Bulk-insert slice metadata received from peers on connect."""
        for m in metas:
            self.upsert_file(
                m["file_id"], m["filename"],
                m["total_blocks"], m["owner_id"]
            )
            self.upsert_slice(m["file_id"], m["block_index"], self_node_id)

    # ── read ──────────────────────────────────────────────────
    def list_files(self, owner_id: str) -> List[dict]:
        rows = self.conn.execute(
            "SELECT * FROM files WHERE owner_id=? ORDER BY created_at DESC",
            (owner_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_slice_map(self, file_id: str) -> List[dict]:
        """Returns [{block_index, peer_node_id}, ...] sorted by block_index."""
        rows = self.conn.execute(
            "SELECT block_index, peer_node_id FROM slices "
            "WHERE file_id=? ORDER BY block_index",
            (file_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def get_file(self, file_id: str) -> Optional[dict]:
        row = self.conn.execute(
            "SELECT * FROM files WHERE file_id=?", (file_id,)
        ).fetchone()
        return dict(row) if row else None

    # ── delete ────────────────────────────────────────────────
    def delete_file(self, file_id: str) -> None:
        self.conn.execute("DELETE FROM files WHERE file_id=?", (file_id,))
        self.conn.commit()

    # ── close ─────────────────────────────────────────────────
    def close(self) -> None:
        self.conn.close()
