from __future__ import annotations

import hashlib
import hmac
import os

from src.app.security.interfaces import PasswordHasherInterface


class PBKDF2PasswordHasher(PasswordHasherInterface):
    def hash_password(self, password: str) -> str:
        salt = os.urandom(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
        return f"{salt.hex()}:{digest.hex()}"

    def verify_password(self, password: str, stored_hash: str) -> bool:
        try:
            salt_hex, digest_hex = stored_hash.split(":", 1)
        except ValueError:
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
        return hmac.compare_digest(candidate, expected)
