"""存储与缓存管理模块（storage/ 目录体系 + root.db + sha256sum.db）

目录布局（运行时生成，整体被 .gitignore 排除）：
    storage/
    ├── root.db                          # sqlite3 元数据库（songs / files 表）
    ├── sha256sum.db                     # sqlite3 哈希追踪库（file_hashes 表）
    ├── <song_id>/                       # 每首歌曲一个文件夹
    │   ├── <歌曲名>.<ext>                # 各音质音频（扩展名冲突时加音质段，见下）
    │   ├── cover.jpg                    # 专辑封面
    │   └── lyric.lrc                    # 歌词（原文）
    └── albums/<album_id>/<song_id>/     # 归属专辑的歌曲另存一份副本（结构同上）

命名规则：默认以「歌曲名.实际扩展名」命名；若同一扩展名已被另一音质占用
（如 standard 与 exhigh 均为 mp3），则追加音质段：「歌曲名.<quality>.<ext>」。

哈希追踪（sha256sum.db）：任何文件进入 storage 时计算并登记 SHA-256
（音频主文件、专辑副本、封面、歌词）；缓存命中对外提供下载前重算比对，
不一致（含无记录）判定为损坏 → 删除该音质文件与副本并重新下载。

缓存语义：以 (song_id, quality) 为缓存键（见 files 表 UNIQUE 约束），命中即复用
文件并累加 songs.visit_count；不再按文件名猜测（修复原 /download 缓存检查失效问题）。

清理策略（cleanup，启动时与守护线程周期执行）：
    1. 到期删除：歌曲首次入库（songs.created_at）超过 retention_days（默认 90 天）
       → 删除整个歌曲目录及专辑副本目录，并清理 DB 行（含哈希记录）；
    2. 超限淘汰：全部文件总大小超过 max_total_mb → 按 visit_count 由少到多
       整歌淘汰（含专辑副本与哈希记录），直至总大小回到上限内。

数据库：每次操作独立短连接（sqlite3 标准库，无 ORM），规避跨线程连接问题；
写并发由 sqlite 内置锁兜底（个人规模足够）。
"""

from __future__ import annotations

import difflib
import hashlib
import shutil
import sqlite3
import time
from pathlib import Path
from typing import Any

# 清理周期与保留期默认值
CLEANUP_INTERVAL_SECONDS = 3600
DEFAULT_RETENTION_DAYS = 90


class StorageManager:
    """storage/ 目录与 root.db 的统一管理入口"""

    def __init__(
        self,
        storage_dir: str | Path = "storage",
        max_total_mb: float | None = None,
        retention_days: int = DEFAULT_RETENTION_DAYS,
    ):
        """
        Args:
            storage_dir: 存储根目录（测试时传临时路径）
            max_total_mb: 全部文件总大小上限（MB）；None 表示不启用超限淘汰
            retention_days: 文件保留天数（按歌曲首次入库时间起算）
        """
        self.storage_dir = Path(storage_dir)
        self.albums_dir = self.storage_dir / "albums"
        self.max_total_mb = max_total_mb
        self.retention_days = retention_days
        self.db_path = self.storage_dir / "root.db"
        self.hash_db_path = self.storage_dir / "sha256sum.db"
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self._init_hash_db()

    # ----------- 数据库 -----------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS songs (
                    song_id       INTEGER PRIMARY KEY,
                    name          TEXT NOT NULL DEFAULT '',
                    artists       TEXT NOT NULL DEFAULT '',
                    album_id      INTEGER,
                    album_name    TEXT NOT NULL DEFAULT '',
                    duration      INTEGER NOT NULL DEFAULT 0,
                    visit_count   INTEGER NOT NULL DEFAULT 0,
                    created_at    REAL NOT NULL,
                    last_access_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS files (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    song_id       INTEGER NOT NULL,
                    quality       TEXT NOT NULL,
                    ext           TEXT NOT NULL,
                    path          TEXT NOT NULL UNIQUE,
                    size          INTEGER NOT NULL,
                    downloaded_at REAL NOT NULL,
                    is_copy       INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(song_id, quality, is_copy)
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_files_song ON files(song_id)")

    # ----------- 哈希追踪（sha256sum.db） -----------

    def _connect_hash(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.hash_db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_hash_db(self) -> None:
        with self._connect_hash() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS file_hashes (
                    path          TEXT PRIMARY KEY,
                    sha256        TEXT NOT NULL,
                    size          INTEGER NOT NULL,
                    song_id       INTEGER,
                    quality       TEXT,
                    registered_at REAL NOT NULL
                )
                """
            )

    @staticmethod
    def compute_sha256(path: Path) -> str:
        """流式计算文件 SHA-256（1MB 分块，避免大文件占内存）"""
        digest = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def register_hash(self, path: Path, song_id: int | None = None,
                      quality: str | None = None) -> str:
        """计算文件哈希并登记/更新（任何文件进入 storage 时调用）"""
        sha = self.compute_sha256(path)
        with self._connect_hash() as conn:
            conn.execute(
                """
                INSERT INTO file_hashes (path, sha256, size, song_id, quality, registered_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    sha256 = excluded.sha256,
                    size = excluded.size,
                    song_id = excluded.song_id,
                    quality = excluded.quality,
                    registered_at = excluded.registered_at
                """,
                (self._rel_path(path), sha, path.stat().st_size, song_id, quality, time.time()),
            )
        return sha

    def verify_hash(self, path: Path) -> bool:
        """校验文件完整性：重算 SHA-256 与记录比对；无记录/文件缺失/不一致均为 False"""
        if not path.is_file():
            return False
        with self._connect_hash() as conn:
            row = conn.execute(
                "SELECT sha256 FROM file_hashes WHERE path = ?", (self._rel_path(path),)
            ).fetchone()
        if not row:
            return False
        return self.compute_sha256(path) == row["sha256"]

    def remove_hash(self, path: Path) -> None:
        with self._connect_hash() as conn:
            conn.execute("DELETE FROM file_hashes WHERE path = ?", (self._rel_path(path),))

    def invalidate_file(self, song_id: int, quality: str) -> None:
        """损坏处理：删除指定音质的主文件与专辑副本、files 行及哈希记录

        保留 songs 行与封面/歌词（其哈希记录不受影响）。
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT path FROM files WHERE song_id = ? AND quality = ?",
                (song_id, quality),
            ).fetchall()
            conn.execute(
                "DELETE FROM files WHERE song_id = ? AND quality = ?", (song_id, quality)
            )
        for row in rows:
            path = self.storage_dir / row["path"]
            path.unlink(missing_ok=True)
            self.remove_hash(path)

    # ----------- 路径 -----------

    def song_dir(self, song_id: int) -> Path:
        return self.storage_dir / str(song_id)

    def album_song_dir(self, album_id: int, song_id: int) -> Path:
        return self.albums_dir / str(album_id) / str(song_id)

    def _safe_name(self, name: str) -> str:
        """与下载器一致的文件名清洗（此处只做保守替换，避免引入循环依赖）"""
        import re

        name = re.sub(r'[<>:"/\\|?*]', "_", name).strip(" .")
        return (name[:200] or "unknown")

    def resolve_audio_path(self, song_id: int, quality: str, song_name: str, ext: str) -> Path:
        """确定音质文件的落盘路径：默认 歌曲名.ext，扩展名被其他音质占用时加音质段"""
        base = self._safe_name(song_name)
        preferred = self.song_dir(song_id) / f"{base}{ext}"
        occupied = self._query_path_owner(song_id, preferred)
        if occupied is None or occupied == quality:
            return preferred
        return self.song_dir(song_id) / f"{base}.{quality}{ext}"

    def _query_path_owner(self, song_id: int, path: Path) -> str | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT quality FROM files WHERE song_id = ? AND path = ?",
                (song_id, self._rel_path(path)),
            ).fetchone()
        return row["quality"] if row else None

    def _rel_path(self, path: Path) -> str:
        """转成相对 storage 的 POSIX 路径存库，目录整体迁移时记录不失效"""
        return path.resolve().relative_to(self.storage_dir.resolve()).as_posix()

    # ----------- 缓存查询与访问计数 -----------

    def get_cached_file(self, song_id: int, quality: str) -> Path | None:
        """查询 (song_id, quality) 主存储缓存：DB 有记录且文件真实存在才算命中
        （专辑副本不作为命中对象，仅计入总大小）"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT path FROM files WHERE song_id = ? AND quality = ? AND is_copy = 0",
                (song_id, quality),
            ).fetchone()
        if not row:
            return None
        path = self.storage_dir / row["path"]
        return path if path.is_file() else None

    def song_with_file(self, song_id: int, quality: str) -> dict[str, Any] | None:
        """联查歌曲元数据与指定音质文件记录（缓存命中时供响应组装，避免重复上游调用）"""
        with self._connect() as conn:
            song = conn.execute(
                "SELECT * FROM songs WHERE song_id = ?", (song_id,)
            ).fetchone()
            if not song:
                return None
            file = conn.execute(
                "SELECT ext, size, downloaded_at FROM files WHERE song_id = ? AND quality = ? AND is_copy = 0",
                (song_id, quality),
            ).fetchone()
        if not file:
            return None
        result = dict(song)
        result["ext"] = file["ext"]
        result["file_size"] = file["size"]
        return result

    def record_access(self, song_id: int) -> None:
        """缓存命中复用时累加歌曲访问量；歌曲未入库时静默忽略"""
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                "UPDATE songs SET visit_count = visit_count + 1, last_access_at = ? WHERE song_id = ?",
                (now, song_id),
            )

    # ----------- 登记与查询 -----------

    def register_song(self, info: dict[str, Any]) -> None:
        """登记/更新歌曲元数据（重复下载同一首歌时保留累计访问量）"""
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO songs (song_id, name, artists, album_id, album_name,
                                   duration, visit_count, created_at, last_access_at)
                VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?)
                ON CONFLICT(song_id) DO UPDATE SET
                    name = excluded.name,
                    artists = excluded.artists,
                    album_id = excluded.album_id,
                    album_name = excluded.album_name,
                    duration = excluded.duration,
                    last_access_at = excluded.last_access_at
                """,
                (
                    int(info["song_id"]), info.get("name", ""), info.get("artists", ""),
                    info.get("album_id"), info.get("album_name", ""),
                    int(info.get("duration", 0)), now, now,
                ),
            )

    def register_file(self, song_id: int, quality: str, path: Path, size: int,
                      is_copy: bool = False) -> None:
        """登记音质文件（is_copy=True 表示 albums/ 下的专辑副本；同键重复登记则更新）"""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO files (song_id, quality, ext, path, size, downloaded_at, is_copy)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(song_id, quality, is_copy) DO UPDATE SET
                    ext = excluded.ext,
                    path = excluded.path,
                    size = excluded.size,
                    downloaded_at = excluded.downloaded_at
                """,
                (song_id, quality, path.suffix, self._rel_path(path), int(size),
                 time.time(), int(is_copy)),
            )

    def song_stats(self, song_id: int) -> dict[str, Any] | None:
        """查询单首歌曲的元数据与访问量（推荐/排行功能的基础查询）"""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM songs WHERE song_id = ?", (song_id,)
            ).fetchone()
        return dict(row) if row else None

    def cached_qualities(self, song_id: int) -> list[dict[str, Any]]:
        """列出已缓存音质版本及其文件大小（推荐功能展示用）"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT quality, ext, size, path FROM files WHERE song_id = ? ORDER BY quality",
                (song_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ----------- 智能推荐 -----------

    NAME_SIMILAR_THRESHOLD = 0.55  # 歌名近似判定阈值（difflib 相似度）

    def recommend_for(self, song_id: int, limit: int = 5) -> list[dict[str, Any]]:
        """基于 root.db 特征数据的智能推荐（每次最多 limit 首，默认 5）

        打分维度（仅候选缓存中已有的歌曲，排除自身）：
        - 同歌手（歌手名任一交集）：+3.0
        - 歌名近似（difflib 相似度 ≥ 0.55）：+ 相似度 × 2.0
        - 同专辑：+1.0
        候选不足 limit 时按访问量降序以热门缓存歌曲兜底补足。
        每项展示实际缓存的音质版本列表（cached=True 并标注文件大小）。
        """
        origin = self.song_stats(song_id)
        if not origin:
            return []

        origin_artists = set(filter(None, (origin.get("artists") or "").replace("/", " ").split()))
        origin_name = origin.get("name") or ""

        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT s.song_id AS song_id, s.name AS name, s.artists AS artists,
                       s.album_id AS album_id, s.album_name AS album_name,
                       s.visit_count AS visit_count
                FROM songs s
                JOIN files f ON f.song_id = s.song_id AND f.is_copy = 0
                WHERE s.song_id != ?
                GROUP BY s.song_id
                """,
                (song_id,),
            ).fetchall()

        scored: list[tuple[float, int, int, sqlite3.Row]] = []
        for r in rows:
            score = 0.0
            cand_artists = set(filter(None, (r["artists"] or "").replace("/", " ").split()))
            if origin_artists & cand_artists:
                score += 3.0
            ratio = difflib.SequenceMatcher(None, origin_name, r["name"] or "").ratio()
            if ratio >= self.NAME_SIMILAR_THRESHOLD:
                score += ratio * 2.0
            if origin.get("album_id") is not None and r["album_id"] == origin["album_id"]:
                score += 1.0
            scored.append((score, int(r["visit_count"]), int(r["song_id"]), r))

        scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
        picked = scored[:limit]
        # 补足策略：相关候选不足时按访问量降序取热门缓存歌曲
        if len(picked) < limit:
            picked_ids = {t[2] for t in picked}
            hot = sorted(
                (r for r in rows if int(r["song_id"]) not in picked_ids),
                key=lambda r: (-int(r["visit_count"]), int(r["song_id"])),
            )
            for r in hot:
                if len(picked) >= limit:
                    break
                picked.append((0.0, int(r["visit_count"]), int(r["song_id"]), r))

        results: list[dict[str, Any]] = []
        for _score, _visits, sid, _r in picked[:limit]:
            qualities = [
                {"quality": q["quality"], "ext": q["ext"], "size": q["size"], "cached": True}
                for q in self.cached_qualities(sid)
            ]
            results.append({
                "song_id": sid,
                "name": _r["name"],
                "artists": _r["artists"],
                "album_name": _r["album_name"],
                "qualities": qualities,
            })
        return results

    # ----------- 清理 -----------

    def total_size(self) -> int:
        """全部已登记文件的总字节数（含专辑副本）"""
        with self._connect() as conn:
            row = conn.execute("SELECT COALESCE(SUM(size), 0) AS total FROM files").fetchone()
        return int(row["total"])

    def _remove_song(self, conn: sqlite3.Connection, song_id: int) -> int:
        """按歌曲粒度删除：song 目录 + 专辑副本目录 + files 行 + 哈希记录；返回释放字节数"""
        row = conn.execute(
            "SELECT COALESCE(SUM(size), 0) AS total FROM files WHERE song_id = ?",
            (song_id,),
        ).fetchone()
        freed = int(row["total"])
        conn.execute("DELETE FROM files WHERE song_id = ?", (song_id,))
        conn.execute("DELETE FROM songs WHERE song_id = ?", (song_id,))
        with self._connect_hash() as hconn:
            hconn.execute("DELETE FROM file_hashes WHERE song_id = ?", (song_id,))
        shutil.rmtree(self.song_dir(song_id), ignore_errors=True)
        # 专辑副本目录（song 可能归属专辑，逐个探测删除）
        for album_dir in self.albums_dir.glob(f"*/{song_id}"):
            shutil.rmtree(album_dir, ignore_errors=True)
        return freed

    def cleanup(self, now: float | None = None) -> dict[str, Any]:
        """执行清理：到期删除 + 超限淘汰，返回统计供日志输出"""
        now = now if now is not None else time.time()
        removed_expired = 0
        removed_evicted = 0
        with self._connect() as conn:
            # 1) 到期删除（按歌曲首次入库时间）
            deadline = now - self.retention_days * 86400
            expired = conn.execute(
                "SELECT song_id FROM songs WHERE created_at <= ?", (deadline,)
            ).fetchall()
            for row in expired:
                self._remove_song(conn, row["song_id"])
                removed_expired += 1

            # 2) 超限淘汰（按 visit_count 由少到多整歌淘汰）
            if self.max_total_mb is not None:
                max_bytes = int(self.max_total_mb * 1024 * 1024)
                while True:
                    total = conn.execute(
                        "SELECT COALESCE(SUM(size), 0) AS total FROM files"
                    ).fetchone()["total"]
                    if total <= max_bytes:
                        break
                    victim = conn.execute(
                        """
                        SELECT s.song_id FROM songs s
                        JOIN files f ON f.song_id = s.song_id
                        GROUP BY s.song_id
                        ORDER BY s.visit_count ASC, s.song_id ASC
                        LIMIT 1
                        """
                    ).fetchone()
                    if victim is None:
                        break  # DB 无登记文件但磁盘仍超限（异常态），交由人工处理
                    self._remove_song(conn, victim["song_id"])
                    removed_evicted += 1
        return {
            "expired_removed": removed_expired,
            "evicted_removed": removed_evicted,
            "total_size": self.total_size(),
        }

    def start_cleanup_daemon(self, interval: int = CLEANUP_INTERVAL_SECONDS) -> None:
        """启动守护清理线程：立即执行一次，此后按周期循环（随主进程退出）"""
        import threading

        def _loop() -> None:
            while True:
                try:
                    stats = self.cleanup()
                    if stats["expired_removed"] or stats["evicted_removed"]:
                        print(
                            f"[STORAGE] 清理完成: 到期删除 {stats['expired_removed']} 首, "
                            f"超限淘汰 {stats['evicted_removed']} 首, "
                            f"当前总大小 {stats['total_size']} 字节"
                        )
                except Exception as e:  # 清理失败不影响服务
                    print(f"[STORAGE] 清理任务异常: {e}")
                time.sleep(interval)

        threading.Thread(target=_loop, daemon=True, name="storage-cleanup").start()

    # ----------- 辅助 -----------

    def save_cover(self, song_id: int, data: bytes) -> Path:
        """保存专辑封面"""
        path = self.song_dir(song_id) / "cover.jpg"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def save_lyric(self, song_id: int, lyric: str) -> Path | None:
        """保存歌词（空内容跳过）"""
        if not lyric or not lyric.strip():
            return None
        path = self.song_dir(song_id) / "lyric.lrc"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(lyric, encoding="utf-8")
        return path
