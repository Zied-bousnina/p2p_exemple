"""
SlicingEngine — handles the full file lifecycle:
  - slice_file():   encrypt → split into N-byte blocks → return slice list
  - distribute():   assign slices to peers round-robin → push via HTTP
  - fetch_file():   pull slices from peers → sort → concat → decrypt → save
"""
import json
import struct
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

import httpx

from crypto_manager import CryptoManager

BLOCK_SIZE = 512 * 1024  # 512 KB per slice (configurable via env)


class SlicingEngine:
    def __init__(self, crypto: CryptoManager,
                 block_size: int = BLOCK_SIZE,
                 owner_id: str = "user"):
        self.crypto = crypto
        self.block_size = block_size
        self.owner_id = owner_id

    # ── WRITE: encrypt → split → build slice bytes ────────────
    def slice_file(self, file_path: str) -> Tuple[List[dict], str]:
        """
        Encrypt the file and split into slices.
        Returns (list_of_slice_dicts, file_id).
        Each dict has: file_id, block_index, total_blocks, filename, raw (bytes)
        """
        raw = Path(file_path).read_bytes()
        filename = Path(file_path).name
        file_id = str(uuid.uuid4())

        # Step 1 — encrypt
        ciphertext = self.crypto.encrypt(raw)

        # Step 2 — split into fixed blocks
        blocks = [
            ciphertext[i: i + self.block_size]
            for i in range(0, len(ciphertext), self.block_size)
        ]
        total = len(blocks)

        slices = []
        for idx, block in enumerate(blocks):
            meta = {
                "file_id":      file_id,
                "block_index":  idx,
                "total_blocks": total,
                "filename":     filename,
                "owner_id":     self.owner_id,
                "timestamp":    datetime.now(timezone.utc).isoformat(),
            }
            meta_bytes = json.dumps(meta, separators=(",", ":")).encode()
            # 4-byte big-endian header length prefix
            raw_slice = struct.pack(">I", len(meta_bytes)) + meta_bytes + block
            slices.append({
                "file_id":      file_id,
                "block_index":  idx,
                "total_blocks": total,
                "filename":     filename,
                "owner_id":     self.owner_id,
                "raw":          raw_slice,
            })

        return slices, file_id

    # ── DISTRIBUTE: round-robin push to peers ─────────────────
    async def distribute(self, slices: List[dict], peers: List[dict]) -> dict:
        """
        Push slices to peers round-robin via HTTP POST /slices.
        peers = [{"node_id": ..., "host": ..., "port": ...}, ...]
        Returns {peer_node_id: [block_indices]} assignment map.
        """
        if not peers:
            raise RuntimeError("No peers available to distribute slices.")

        assignment: dict = {}
        async with httpx.AsyncClient(timeout=30.0) as client:
            for i, sl in enumerate(slices):
                peer = peers[i % len(peers)]
                url = f"http://{peer['host']}:{peer['port']}/slices"
                resp = await client.post(
                    url,
                    data={
                        "file_id":     sl["file_id"],
                        "block_index": str(sl["block_index"]),
                        "owner_id":    sl["owner_id"],
                    },
                    files={"file": (f"slice_{sl['block_index']}", sl["raw"],
                                    "application/octet-stream")},
                )
                resp.raise_for_status()
                sl["assigned_peer"] = peer["node_id"]
                assignment.setdefault(peer["node_id"], []).append(sl["block_index"])

        return assignment

    # ── READ: fetch slices from peers → reassemble → decrypt ──
    async def fetch_file(self, file_id: str, slice_map: List[dict],
                         peers_info: dict, output_path: str) -> str:
        """
        slice_map = list of {block_index, peer_node_id} from cache DB.
        peers_info = {node_id: {host, port}} dict.
        Fetches all blocks, sorts, decrypts, saves to output_path.
        """
        raw_blocks: List[Tuple[int, bytes]] = []

        async with httpx.AsyncClient(timeout=30.0) as client:
            for entry in slice_map:
                idx = entry["block_index"]
                node_id = entry["peer_node_id"]
                peer = peers_info.get(node_id)
                if not peer:
                    raise RuntimeError(
                        f"Peer {node_id} is offline — cannot fetch block {idx}."
                    )
                url = (f"http://{peer['host']}:{peer['port']}"
                       f"/slices/{file_id}/{idx}")
                resp = await client.get(url)
                resp.raise_for_status()
                raw_slice = resp.content

                # parse header to get block_index (double-check)
                header_len = struct.unpack(">I", raw_slice[:4])[0]
                # skip metadata, keep only ciphertext
                ciphertext_block = raw_slice[4 + header_len:]
                raw_blocks.append((idx, ciphertext_block))

        # Sort by block_index and concatenate ciphertext
        raw_blocks.sort(key=lambda x: x[0])
        ciphertext_buf = b"".join(data for _, data in raw_blocks)

        # Decrypt
        plaintext = self.crypto.decrypt(ciphertext_buf)

        Path(output_path).write_bytes(plaintext)
        return output_path

    # ── helper ────────────────────────────────────────────────
    @staticmethod
    def parse_metadata(raw_slice: bytes) -> dict:
        header_len = struct.unpack(">I", raw_slice[:4])[0]
        return json.loads(raw_slice[4:4 + header_len].decode())
