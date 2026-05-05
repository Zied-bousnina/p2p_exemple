"""
CryptoManager — AES-256 symmetric encryption using Fernet.
Key is derived from user password via Scrypt KDF.
The secret key NEVER leaves the user's machine.
"""
import os
import base64
from pathlib import Path
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from cryptography.hazmat.backends import default_backend


SALT_FILE = ".p2p_salt"
KEY_FILE  = ".p2p_key"


class CryptoManager:
    def __init__(self, password: str, data_dir: str = "."):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.salt = self._load_or_create_salt()
        self.fernet = self._build_fernet(password)

    # ── internal ──────────────────────────────────────────────
    def _load_or_create_salt(self) -> bytes:
        salt_path = self.data_dir / SALT_FILE
        if salt_path.exists():
            return salt_path.read_bytes()
        salt = os.urandom(16)
        salt_path.write_bytes(salt)
        return salt

    def _build_fernet(self, password: str) -> Fernet:
        kdf = Scrypt(
            salt=self.salt,
            length=32,
            n=2**14,
            r=8,
            p=1,
            backend=default_backend()
        )
        key_bytes = kdf.derive(password.encode("utf-8"))
        b64_key = base64.urlsafe_b64encode(key_bytes)
        return Fernet(b64_key)

    # ── public ────────────────────────────────────────────────
    def encrypt(self, data: bytes) -> bytes:
        """Encrypt raw bytes → ciphertext bytes."""
        return self.fernet.encrypt(data)

    def decrypt(self, token: bytes) -> bytes:
        """Decrypt ciphertext bytes → raw bytes. Raises on tamper."""
        try:
            return self.fernet.decrypt(token)
        except InvalidToken:
            raise ValueError("Decryption failed — wrong key or corrupted data.")

    @staticmethod
    def is_first_run(data_dir: str = ".") -> bool:
        return not (Path(data_dir) / SALT_FILE).exists()
