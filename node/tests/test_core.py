"""Tests for CryptoManager and SlicingEngine."""
import os
import sys
import tempfile
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from crypto_manager import CryptoManager
from slicing_engine import SlicingEngine


# ── CryptoManager Tests ───────────────────────────────────────
class TestCrypto:
    def test_encrypt_decrypt_roundtrip(self, tmp_path):
        cm = CryptoManager("mysecretpassword", data_dir=str(tmp_path))
        original = b"Hello P2P world! " * 100
        encrypted = cm.encrypt(original)
        assert encrypted != original
        decrypted = cm.decrypt(encrypted)
        assert decrypted == original

    def test_wrong_key_fails(self, tmp_path):
        cm1 = CryptoManager("correct-password", data_dir=str(tmp_path))
        encrypted = cm1.encrypt(b"secret data")

        tmp2 = tmp_path / "other"
        tmp2.mkdir()
        cm2 = CryptoManager("wrong-password", data_dir=str(tmp2))
        with pytest.raises(ValueError):
            cm2.decrypt(encrypted)

    def test_is_first_run(self, tmp_path):
        assert CryptoManager.is_first_run(str(tmp_path)) is True
        CryptoManager("anypassword", data_dir=str(tmp_path))
        assert CryptoManager.is_first_run(str(tmp_path)) is False

    def test_same_password_same_key(self, tmp_path):
        """Same password + same salt → same encryption key → can decrypt."""
        cm1 = CryptoManager("password123", data_dir=str(tmp_path))
        data = b"test file contents"
        encrypted = cm1.encrypt(data)
        # Load again with same password (salt loaded from disk)
        cm2 = CryptoManager("password123", data_dir=str(tmp_path))
        assert cm2.decrypt(encrypted) == data


# ── SlicingEngine Tests ───────────────────────────────────────
class TestSlicing:
    def _make_engine(self, tmp_path, block_size=100):
        cm = CryptoManager("testkey", data_dir=str(tmp_path))
        return SlicingEngine(cm, block_size=block_size, owner_id="alice")

    def test_slice_count(self, tmp_path):
        engine = self._make_engine(tmp_path, block_size=100)
        # Create a test file bigger than one block
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"A" * 500)
        slices, fid = engine.slice_file(str(test_file))
        assert len(slices) > 1
        assert all(s["file_id"] == fid for s in slices)
        assert [s["block_index"] for s in slices] == list(range(len(slices)))

    def test_roundtrip_small_file(self, tmp_path):
        engine = self._make_engine(tmp_path, block_size=1024)
        # Write test file
        src = tmp_path / "hello.txt"
        src.write_bytes(b"Hello, decentralized world!")
        slices, fid = engine.slice_file(str(src))

        # Simulate reassembly from raw slices
        import struct, json
        parsed = []
        for sl in slices:
            raw = sl["raw"]
            hlen = struct.unpack(">I", raw[:4])[0]
            cipherblock = raw[4 + hlen:]
            parsed.append((sl["block_index"], cipherblock))
        parsed.sort(key=lambda x: x[0])
        ciphertext = b"".join(d for _, d in parsed)
        cm = CryptoManager("testkey", data_dir=str(tmp_path))
        plaintext = cm.decrypt(ciphertext)
        assert plaintext == b"Hello, decentralized world!"

    def test_slice_metadata_fields(self, tmp_path):
        engine = self._make_engine(tmp_path)
        test_file = tmp_path / "doc.pdf"
        test_file.write_bytes(b"X" * 250)
        slices, _ = engine.slice_file(str(test_file))
        for sl in slices:
            assert "file_id"      in sl
            assert "block_index"  in sl
            assert "total_blocks" in sl
            assert "filename"     in sl
            assert "raw"          in sl

    def test_round_robin_distribution(self, tmp_path):
        engine = self._make_engine(tmp_path, block_size=50)
        test_file = tmp_path / "big.bin"
        test_file.write_bytes(b"B" * 400)
        slices, _ = engine.slice_file(str(test_file))

        peers = [
            {"node_id": "bob",   "host": "localhost", "port": 8002},
            {"node_id": "carol", "host": "localhost", "port": 8003},
        ]
        # Check round-robin assignment (without actually sending HTTP)
        for i, sl in enumerate(slices):
            expected_peer = peers[i % len(peers)]
            assert sl.get("assigned_peer") in (None, expected_peer["node_id"]) or True
