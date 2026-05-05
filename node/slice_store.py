"""
SliceStore — saves, retrieves and deletes slice files on the local filesystem.
Each slice is stored as:  {store_dir}/{owner_id}/{file_id}_{block_index}.slice
"""
import json
import struct
from pathlib import Path
from typing import List, Optional


class SliceStore:
    def __init__(self, store_dir: str = "/data/slices"):
        self.store_dir = Path(store_dir)
        self.store_dir.mkdir(parents=True, exist_ok=True)

    # ── path helper ───────────────────────────────────────────
    def _path(self, owner_id: str, file_id: str, block_index: int) -> Path:
        d = self.store_dir / owner_id
        d.mkdir(parents=True, exist_ok=True)
        return d / f"{file_id}_{block_index}.slice"

    # ── write ─────────────────────────────────────────────────
    def save_slice(self, owner_id: str, file_id: str,
                   block_index: int, raw: bytes) -> None:
        """Save raw slice bytes (header + ciphertext) to disk."""
        self._path(owner_id, file_id, block_index).write_bytes(raw)

    # ── read ──────────────────────────────────────────────────
    def get_slice(self, owner_id: str, file_id: str,
                  block_index: int) -> Optional[bytes]:
        p = self._path(owner_id, file_id, block_index)
        return p.read_bytes() if p.exists() else None

    # ── delete ────────────────────────────────────────────────
    def delete_file(self, file_id: str) -> int:
        """Delete ALL slices for a given file_id across all owners. Returns count."""
        count = 0
        for p in self.store_dir.rglob(f"{file_id}_*.slice"):
            p.unlink()
            count += 1
        return count

    # ── list ──────────────────────────────────────────────────
    def list_for_user(self, owner_id: str) -> List[dict]:
        """Return metadata dicts for every slice belonging to owner_id."""
        user_dir = self.store_dir / owner_id
        if not user_dir.exists():
            return []
        results = []
        for p in user_dir.glob("*.slice"):
            try:
                raw = p.read_bytes()
                header_len = struct.unpack(">I", raw[:4])[0]
                meta = json.loads(raw[4:4 + header_len].decode())
                results.append(meta)
            except Exception:
                continue
        return results

    def list_all(self) -> List[dict]:
        """Return metadata for every slice on this node."""
        results = []
        for p in self.store_dir.rglob("*.slice"):
            try:
                raw = p.read_bytes()
                header_len = struct.unpack(">I", raw[:4])[0]
                meta = json.loads(raw[4:4 + header_len].decode())
                results.append(meta)
            except Exception:
                continue
        return results
