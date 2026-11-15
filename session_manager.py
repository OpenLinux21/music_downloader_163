"""防刷会话与下载排行榜模块（storage/cookies.db + HMAC-SHA256 签名 Cookie）

防刷 Cookie 机制：
- 服务端首次使用时生成 32 字节随机密钥，持久化于 storage/.ranking_secret
  （0600 权限，仅服务端持有，绝不下发/写日志/入 Git——storage 整体已被 .gitignore 排除）；
- Cookie 载荷 = 随机会话 ID.签发时间.过期时间（epoch 秒），签名 = HMAC-SHA256(密钥, 载荷)；
  Cookie 值 = f"{载荷}.{签名 hex}"，HttpOnly + SameSite=Lax + Path=/，默认 7 天过期；
- 校验顺序：先 hmac.compare_digest 验签，再检查时效；无效/过期即重新签发新会话。
  外部脚本无法批量获取可用 Cookie（签名密钥不下发），客户端无法自行构造合法 Cookie。

排行榜（storage/cookies.db）：
- sessions 表：签发记录（会话 ID 主键、签发/过期时间、来源 IP）；
- downloads 表：真实完成的下载交付记录，PRIMARY KEY (session_id, song_id)
  → 同一会话对同一歌曲只计一次（识别反复下载刷排名）；
- 排名口径：统计窗口内（默认 30 天，"最近"语义）每个歌曲的独立会话数，
  降序取 TOP 20，计数并列按歌曲名排序；已清理歌曲（无 songs 元数据）不入榜。
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

COOKIE_NAME = "nu_session"
SECRET_FILE = ".ranking_secret"
SESSION_TTL_SECONDS = 7 * 86400          # 会话有效期：7 天
RANK_WINDOW_DAYS = 30                     # 排行榜统计窗口（"最近"语义）
RANK_SIZE = 20                            # 榜单容量


class SessionManager:
    """防刷会话签发/校验与下载排行榜"""

    def __init__(self, storage_dir: str | Path = "storage",
                 root_db_path: str | Path | None = None,
                 ttl_seconds: int = SESSION_TTL_SECONDS,
                 rank_window_days: int = RANK_WINDOW_DAYS):
        self.storage_dir = Path(storage_dir)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self.secret_path = self.storage_dir / SECRET_FILE
        self.db_path = self.storage_dir / "cookies.db"
        # 排行榜需联查歌曲元数据（root.db 的 songs 表）
        self.root_db_path = Path(root_db_path) if root_db_path else (self.storage_dir / "root.db")
        self.ttl_seconds = ttl_seconds
        self.rank_window_days = rank_window_days
        self._secret = self._load_or_create_secret()
        self._init_db()

    # ----------- 密钥 -----------

    def _load_or_create_secret(self) -> bytes:
        """读取或一次性生成服务端签名密钥（0600 权限，绝不外发）"""
        if self.secret_path.exists():
            return self.secret_path.read_bytes().strip()
        secret = secrets.token_hex(32).encode()
        fd = os.open(self.secret_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(secret)
        return secret

    # ----------- 数据库 -----------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    issued_at  REAL NOT NULL,
                    expires_at REAL NOT NULL,
                    ip         TEXT NOT NULL DEFAULT ''
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS downloads (
                    session_id    TEXT NOT NULL,
                    song_id       INTEGER NOT NULL,
                    quality       TEXT NOT NULL DEFAULT '',
                    downloaded_at REAL NOT NULL,
                    PRIMARY KEY (session_id, song_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_downloads_song_time ON downloads(song_id, downloaded_at)"
            )

    # ----------- 签发与校验 -----------

    def _sign(self, payload: str) -> str:
        return hmac.new(self._secret, payload.encode(), hashlib.sha256).hexdigest()

    def issue_token(self, ip: str = "") -> Tuple[str, str]:
        """签发新会话：返回 (cookie 值, 会话 ID)，并登记 sessions 行"""
        session_id = secrets.token_urlsafe(16)
        now = time.time()
        expires_at = now + self.ttl_seconds
        payload = f"{session_id}.{int(now)}.{int(expires_at)}"
        token = f"{payload}.{self._sign(payload)}"
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sessions (session_id, issued_at, expires_at, ip) VALUES (?, ?, ?, ?)",
                (session_id, now, expires_at, ip),
            )
        return token, session_id

    def resolve_token(self, token: Optional[str]) -> Optional[str]:
        """校验 Cookie 值：先验签（compare_digest）再检查时效，有效返回会话 ID，否则 None"""
        if not token:
            return None
        parts = token.split(".")
        if len(parts) != 4:
            return None
        session_id, issued_s, expires_s, signature = parts
        payload = f"{session_id}.{issued_s}.{expires_s}"
        expected = self._sign(payload)
        if not hmac.compare_digest(signature, expected):
            return None
        try:
            expires_at = int(expires_s)
        except ValueError:
            return None
        if time.time() >= expires_at:
            return None
        return session_id

    # ----------- 排行榜 -----------

    def record_download(self, session_id: str, song_id: int, quality: str = "") -> None:
        """记录一次真实完成的下载交付（同会话同歌曲去重，仅刷新时间）"""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO downloads (session_id, song_id, quality, downloaded_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(session_id, song_id) DO UPDATE SET
                    quality = excluded.quality,
                    downloaded_at = excluded.downloaded_at
                """,
                (session_id, song_id, quality, time.time()),
            )

    def rank_top(self, size: int = RANK_SIZE) -> List[Dict[str, Any]]:
        """下载排行榜：窗口内独立会话数降序，并列按歌曲名排序，TOP size

        联查 root.db 的 songs 表取名称/歌手；已清理（无元数据）的歌曲不入榜。
        """
        window_start = time.time() - self.rank_window_days * 86400
        conn = self._connect()
        try:
            conn.execute("ATTACH DATABASE ? AS meta", (str(self.root_db_path),))
            rows = conn.execute(
                """
                SELECT d.song_id            AS song_id,
                       m.name               AS name,
                       m.artists            AS artists,
                       COUNT(*)             AS session_count
                FROM downloads d
                JOIN meta.songs m ON m.song_id = d.song_id
                WHERE d.downloaded_at >= ?
                GROUP BY d.song_id
                ORDER BY session_count DESC, m.name ASC
                LIMIT ?
                """,
                (window_start, size),
            ).fetchall()
        finally:
            conn.execute("DETACH DATABASE meta")
            conn.close()
        return [dict(r) for r in rows]

    def session_download_count(self, session_id: str, song_id: int) -> int:
        """调试/测试辅助：查询指定会话对指定歌曲是否已有计数"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM downloads WHERE session_id = ? AND song_id = ?",
                (session_id, song_id),
            ).fetchone()
        return 1 if row else 0
