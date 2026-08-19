# -*- coding: utf-8 -*-
"""crypto.py — Mã hoá/giải mã mật khẩu người dùng bằng Fernet (đối xứng).

Nguyên tắc bảo mật (theo yêu cầu #2):
  - KHÔNG BAO GIỜ lưu mật khẩu dạng plaintext ra đĩa.
  - Key giải mã đọc từ biến môi trường ``DNU_FERNET_KEY`` (đặt trong .env).
  - Nếu chưa có key, tự sinh 1 key mới và ghi vào .env (chỉ dùng cho localhost).

Fernet đảm bảo:
  - AES-128-CBC + HMAC-SHA256 (authenticated encryption).
  - Token có timestamp -> có thể set TTL nếu cần.
"""
from __future__ import annotations

import logging
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger("crypto")

_ENV_KEY_NAME = "DNU_FERNET_KEY"


def _read_env(env_path: Path) -> dict[str, str]:
    env: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    return env


def _ensure_key(env_path: Path) -> bytes:
    """Đọc key từ .env; nếu chưa có thì sinh mới + ghi bổ sung vào .env."""
    env = _read_env(env_path)
    key = env.get(_ENV_KEY_NAME, "").strip()
    if key:
        return key.encode()

    new_key = Fernet.generate_key()
    logger.warning("Chưa có %s trong .env — sinh key mới và ghi vào .env.",
                   _ENV_KEY_NAME)
    with env_path.open("a", encoding="utf-8") as f:
        f.write(f"\n{_ENV_KEY_NAME}={new_key.decode()}\n")
    return new_key


class PasswordCipher:
    """Bọc Fernet để mã hoá/giải mã mật khẩu. Khởi tạo 1 lần, dùng lại."""

    def __init__(self, env_path: str = ".env") -> None:
        self._fernet = Fernet(_ensure_key(Path(env_path)))

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str | None:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except (InvalidToken, ValueError):
            logger.error("Không giải mã được mật khẩu (token hỏng hoặc sai key).")
            return None
