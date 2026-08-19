# -*- coding: utf-8 -*-
"""session_store.py — Quản lý phiên người dùng CÔ LẬP (multi-user safe).

Yêu cầu #6: nhiều người dùng cùng lúc không được conflict/lag. Mỗi người dùng
= 1 ``UserSession`` với UUID riêng, chứa creds + trạng thái job riêng biệt.
KHÔNG có state toàn cục dùng chung -> không race condition.

Thiết kế:
  - ``SessionRegistry``: dict[session_id -> UserSession], bảo vệ bằng
    ``asyncio.Lock`` khi tạo/xoá.
  - ``JobState``: tiến độ 1 lần chạy (checkpoint), an toàn để đọc lại khi
    client reload trang.
  - Cleanup: giải phóng phiên hết hạn -> tránh rò rỉ RAM (Graceful Shutdown).
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class JobStatus(str, Enum):
    IDLE = "idle"
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


@dataclass
class JobState:
    """Trạng thái 1 lần chạy autopilot — checkpoint để reload lại được."""
    job_id: str
    status: JobStatus = JobStatus.IDLE
    total: int = 0
    completed: int = 0
    events: list[dict[str, Any]] = field(default_factory=list)
    results: list[dict[str, Any]] = field(default_factory=list)
    error: Optional[str] = None
    # Hàng đợi sự kiện để SSE stream realtime (bounded để chống phình RAM).
    queue: "asyncio.Queue[dict]" = field(default_factory=lambda: asyncio.Queue(maxsize=1000))

    def snapshot(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "status": self.status.value,
            "total": self.total,
            "completed": self.completed,
            "results": self.results,
            "error": self.error,
        }


@dataclass
class UserSession:
    """Toàn bộ state của 1 người dùng — cô lập hoàn toàn."""
    session_id: str
    email: str
    creds: dict[str, Any]                 # cookie + csrf (in-memory)
    created_at: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    job: Optional[JobState] = None
    # Ngữ cảnh đề thi đang mở (ExamContext) — giữ để dựng payload khi nộp.
    exam_ctx: Optional[Any] = None

    def touch(self) -> None:
        self.last_seen = time.time()


class SessionRegistry:
    """Kho phiên toàn cục, thread/async-safe."""

    def __init__(self, ttl_seconds: int = 3 * 3600) -> None:
        self._sessions: dict[str, UserSession] = {}
        self._lock = asyncio.Lock()
        self._ttl = ttl_seconds

    async def create(self, email: str, creds: dict) -> UserSession:
        async with self._lock:
            sid = uuid.uuid4().hex
            sess = UserSession(session_id=sid, email=email, creds=creds)
            self._sessions[sid] = sess
            return sess

    async def get(self, session_id: str) -> Optional[UserSession]:
        async with self._lock:
            sess = self._sessions.get(session_id)
            if sess:
                sess.touch()
            return sess

    async def remove(self, session_id: str) -> None:
        async with self._lock:
            self._sessions.pop(session_id, None)

    async def cleanup_expired(self) -> int:
        """Xoá phiên quá hạn -> tránh zombie/rò rỉ RAM."""
        now = time.time()
        async with self._lock:
            expired = [sid for sid, s in self._sessions.items()
                       if now - s.last_seen > self._ttl]
            for sid in expired:
                self._sessions.pop(sid, None)
            return len(expired)


# Singleton dùng chung cho toàn app.
registry = SessionRegistry()
