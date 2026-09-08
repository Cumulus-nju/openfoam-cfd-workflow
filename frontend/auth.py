"""
UrbanWind CFD — 认证模块（登录 / 注册 / 会话）

零第三方依赖：
  - 密码哈希：PBKDF2-HMAC-SHA256（标准库 hashlib），随机盐 + 120k 迭代
  - 会话令牌：secrets.token_urlsafe(32)，内存存储 + 固定有效期（默认 7 天）
  - 用户存储：frontend/users.json（首次导入自动创建预设管理员账号）

对外接口（供 app.py 使用）：
  auth.register(username, password)      -> (ok, msg)
  auth.login(username, password)         -> (ok, msg, token)
  auth.get_user_by_token(token)          -> dict | None
  auth.logout(token)                     -> None
  auth.change_password(username, old, new) -> (ok, msg)
  auth.list_users()                      -> [username, ...]
"""
from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from .config import (
    AUTH_USERS_FILE,
    ADMIN_DEFAULT_USERNAME,
    ADMIN_DEFAULT_PASSWORD,
    SESSION_TTL,
)

PBKDF2_ITERATIONS = 120_000
USERNAME_RE = None  # 在 _validate_username 中用字符判断，避免 regex 差异

# ── 密码哈希 (PBKDF2) ─────────────────────────────────────────────────────────


def _pbkdf2_hex(password: str, salt: bytes, iterations: int = PBKDF2_ITERATIONS) -> str:
    """PBKDF2-HMAC-SHA256 → hex 字符串（标准库实现，无外部依赖）。"""
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return dk.hex()


def hash_password(password: str) -> Dict[str, Any]:
    salt = secrets.token_bytes(16)
    return {
        "salt": salt.hex(),
        "hash": _pbkdf2_hex(password, salt),
        "iterations": PBKDF2_ITERATIONS,
    }


def verify_password(password: str, record: Dict[str, Any]) -> bool:
    try:
        salt = bytes.fromhex(record["salt"])
        iterations = int(record.get("iterations", PBKDF2_ITERATIONS))
        calc = _pbkdf2_hex(password, salt, iterations)
        return secrets.compare_digest(calc, record["hash"])
    except (KeyError, ValueError):
        return False


# ── 校验规则 ──────────────────────────────────────────────────────────────────


def _validate_username(username: str) -> Tuple[bool, str]:
    if not username or not isinstance(username, str):
        return False, "用户名不能为空"
    if len(username) < 3 or len(username) > 32:
        return False, "用户名长度需为 3-32 个字符"
    # 允许中文、字母、数字、下划线
    for ch in username:
        if not (ch.isalnum() or ch == "_"):
            return False, "用户名只能包含中文、字母、数字和下划线"
    return True, ""


def _validate_password(password: str) -> Tuple[bool, str]:
    if not password or not isinstance(password, str):
        return False, "密码不能为空"
    if len(password) < 6:
        return False, "密码至少 6 位"
    if len(password) > 128:
        return False, "密码过长（最多 128 位）"
    return True, ""


# ── 用户/会话管理 ─────────────────────────────────────────────────────────────


class AuthManager:
    """用户存储 + 会话令牌管理（单实例，线程安全）。"""

    def __init__(self, users_file: Path, admin_user: str = ADMIN_DEFAULT_USERNAME,
                 admin_pass: str = ADMIN_DEFAULT_PASSWORD):
        self._users_file = Path(users_file)
        self._users: Dict[str, Dict[str, Any]] = {}   # username -> record
        self._tokens: Dict[str, Dict[str, Any]] = {}  # token -> payload
        self._lock = threading.Lock()
        self._admin_user = admin_user
        self._admin_pass = admin_pass
        self._load()

    # ── 存储 ──

    def _load(self) -> None:
        """从 users.json 加载；文件不存在时自动创建管理员账号。"""
        if self._users_file.exists():
            try:
                data = json.loads(self._users_file.read_text(encoding="utf-8"))
                self._users = data.get("users", {})
            except (json.JSONDecodeError, OSError) as e:
                # 文件损坏时不覆盖数据，仅记录并降级为内存账号
                print(f"[auth] users.json 解析失败，使用内存账号: {e}")
                self._users = {}
        else:
            self._users_file.parent.mkdir(parents=True, exist_ok=True)
            self._save()
        # 保证管理员账号存在（首次启动创建；已存在则不动）
        if self._admin_user not in self._users:
            self._users[self._admin_user] = {
                **hash_password(self._admin_pass),
                "role": "admin",
                "created_at": time.time(),
            }
            self._save()
            print(f"[auth] 已创建默认管理员账号: {self._admin_user}")

    def _save(self) -> None:
        """原子写入 users.json（先写临时文件再替换）。"""
        tmp = self._users_file.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps({"users": self._users}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        tmp.replace(self._users_file)

    # ── 用户操作 ──

    def register(self, username: str, password: str) -> Tuple[bool, str]:
        ok, msg = _validate_username(username)
        if not ok:
            return False, msg
        ok, msg = _validate_password(password)
        if not ok:
            return False, msg
        with self._lock:
            if username in self._users:
                return False, "用户名已存在，请直接登录或换一个"
            self._users[username] = {
                **hash_password(password),
                "role": "user",
                "created_at": time.time(),
            }
            self._save()
        return True, "注册成功"

    def login(self, username: str, password: str) -> Tuple[bool, str, Optional[str]]:
        with self._lock:
            record = self._users.get(username)
            if record is None or not verify_password(password, record):
                return False, "用户名或密码错误", None
            token = secrets.token_urlsafe(32)
            self._tokens[token] = {
                "username": username,
                "role": record.get("role", "user"),
                "created_at": time.time(),
                "expires_at": time.time() + SESSION_TTL,
            }
        return True, "登录成功", token

    def get_user_by_token(self, token: Optional[str]) -> Optional[Dict[str, Any]]:
        if not token:
            return None
        with self._lock:
            payload = self._tokens.get(token)
            if payload is None:
                return None
            if payload["expires_at"] < time.time():
                self._tokens.pop(token, None)
                return None
            return {"username": payload["username"], "role": payload["role"]}

    def logout(self, token: Optional[str]) -> None:
        if not token:
            return
        with self._lock:
            self._tokens.pop(token, None)

    def change_password(self, username: str, old_password: str,
                        new_password: str) -> Tuple[bool, str]:
        if not _validate_password(new_password)[0]:
            return False, "新密码至少 6 位"
        with self._lock:
            record = self._users.get(username)
            if record is None:
                return False, "用户不存在"
            if not verify_password(old_password, record):
                return False, "当前密码错误"
            self._users[username] = {**record, **hash_password(new_password)}
            self._save()
        return True, "密码已修改"

    def list_users(self) -> list[str]:
        with self._lock:
            return [
                {"username": u, "role": r.get("role", "user"),
                 "created_at": r.get("created_at")}
                for u, r in sorted(self._users.items())
            ]


# 模块级单例（app.py 直接 import 使用）
auth = AuthManager(AUTH_USERS_FILE)
