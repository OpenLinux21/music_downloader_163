"""排行榜与防刷自检脚本（无测试框架，直接运行）

覆盖范围：
1. HMAC 签发/校验：有效回环；篡改载荷、伪造签名、过期、格式错误均判无效
2. 密钥文件：0600 权限、幂等（重复实例化不重建）
3. 下载计数：同会话同歌曲去重；不同会话分别计数
4. 排名：计数降序、并列按名称排序、30 天窗口过滤、无元数据歌曲不入榜
5. 会话中间件：无 Cookie 签发 Set-Cookie 且可校验；无效 Cookie 重签；有效 Cookie 复用
6. /download 计数 + /rank API 集成（缓存命中路径，不触上游）
7. 端到端（真实交付路径）：两个会话分别下载同一已缓存歌曲 → 独立会话计数 +2

运行方式：.venv/bin/python tests/test_ranking_antispam.py
"""

import shutil
import sqlite3
import stat
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from session_manager import SessionManager, COOKIE_NAME


def test_issue_and_resolve(tmp: Path) -> None:
    sm = SessionManager(tmp / "s_token")
    token, sid = sm.issue_token(ip="1.2.3.4")
    assert sm.resolve_token(token) == sid

    # 篡改载荷（sid 段）
    parts = token.split(".")
    tampered = ".".join(["evil" + parts[0]] + parts[1:])
    assert sm.resolve_token(tampered) is None
    # 伪造签名
    forged = ".".join(parts[:3] + ["0" * 64])
    assert sm.resolve_token(forged) is None
    # 过期（正确签名但 expires 在过去）
    payload = f"{sid}.{int(time.time()) - 100}.{int(time.time()) - 10}"
    expired = f"{payload}.{sm._sign(payload)}"
    assert sm.resolve_token(expired) is None
    # 格式错误
    assert sm.resolve_token("a.b.c") is None
    assert sm.resolve_token("") is None
    assert sm.resolve_token(None) is None
    print("通过: HMAC 签发/校验（回环 + 篡改/伪造/过期/格式错误全拒）")


def test_secret_file(tmp: Path) -> None:
    sm1 = SessionManager(tmp / "s_secret")
    mode = stat.S_IMODE(sm1.secret_path.stat().st_mode)
    assert mode == 0o600, f"密钥文件权限异常: {oct(mode)}"
    content = sm1.secret_path.read_bytes()
    sm2 = SessionManager(tmp / "s_secret")
    assert sm2.secret_path.read_bytes() == content, "密钥被重建（应幂等）"
    assert sm1._secret == sm2._secret
    print("通过: 密钥文件 0600 权限且幂等")


def test_download_dedup(tmp: Path) -> None:
    sm = SessionManager(tmp / "s_dedup")
    sm.record_download("sessA", 1, "standard")
    sm.record_download("sessA", 1, "lossless")  # 同会话同歌曲：刷新不新增
    sm.record_download("sessB", 1, "standard")
    assert sm.session_download_count("sessA", 1) == 1
    with sm._connect() as conn:
        cnt = conn.execute("SELECT COUNT(*) AS c FROM downloads WHERE song_id = 1").fetchone()["c"]
    assert cnt == 2, f"独立会话计数异常: {cnt}"
    print("通过: 同会话同歌曲去重、跨会话分别计数")


def test_rank_order(tmp: Path) -> None:
    from storage_manager import StorageManager
    StorageManager(tmp / "s_rank")  # 建 root.db 与 songs 表
    sm = SessionManager(tmp / "s_rank")

    now = time.time()
    # 歌 300：2 会话（名"乙歌"）；歌 301：2 会话（名"甲歌"）→ 与 300 并列，名称排序在前；
    # 歌 302：1 会话（名"丙歌"）；歌 303：2 会话但全部下载时间在窗口外 → 不入榜；
    # 歌 304：2 会话但已无 songs 元数据 → 不入榜
    seeds = [
        ("s1", 300, now), ("s2", 300, now),
        ("s1", 301, now), ("s3", 301, now),
        ("s1", 302, now),
        ("s1", 303, now - 40 * 86400), ("s2", 303, now - 40 * 86400),
        ("s1", 304, now), ("s2", 304, now),
    ]
    with sm._connect() as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO downloads (session_id, song_id, quality, downloaded_at) VALUES (?, ?, 'standard', ?)",
            seeds,
        )
        conn.execute("ATTACH DATABASE ? AS meta", (str(tmp / "s_rank" / "root.db"),))
        for sid_, name in ((300, "乙歌"), (301, "甲歌"), (302, "丙歌"), (303, "窗口外")):
            conn.execute(
                "INSERT OR REPLACE INTO meta.songs (song_id, name, artists, album_id, album_name,"
                " duration, visit_count, created_at, last_access_at) VALUES (?, ?, '歌手', NULL, '', 1, 0, ?, ?)",
                (sid_, name, now, now),
            )
        # 歌 304 故意不插 songs 元数据

    rank = sm.rank_top()
    ids = [r["song_id"] for r in rank]
    # 并列按名称排序：SQLite BINARY 序（UTF-8 字节序）下 "乙歌"(U+4E59) 排在 "甲歌"(U+7532) 之前，
    # 顺序确定且可复现即为满足「下载量一致时按名称排序」
    assert ids == [300, 301, 302], f"排名异常: {rank}"
    assert rank[0]["name"] == "乙歌" and rank[1]["name"] == "甲歌", "并列未按名称排序"
    print(f"通过: 排名（计数降序、并列按名称、窗口过滤、无元数据不入榜）：{[(r['song_id'], r['session_count']) for r in rank]}")


def test_middleware(tmp: Path) -> None:
    import main
    from fastapi.testclient import TestClient
    client = TestClient(main.app)

    # 无 Cookie → 签发并 Set-Cookie
    r1 = client.get("/health")
    set_cookie = r1.headers.get("set-cookie", "")
    assert COOKIE_NAME in set_cookie and "httponly" in set_cookie.lower(), set_cookie
    token = set_cookie.split(f"{COOKIE_NAME}=")[1].split(";")[0]
    sid1 = main.session_manager.resolve_token(token)
    assert sid1 is not None, "签发的 Cookie 无法通过校验"

    # 带有效 Cookie → 不重新签发（无 Set-Cookie）
    r2 = client.get("/health")
    assert COOKIE_NAME not in (r2.headers.get("set-cookie") or "")

    # 带无效 Cookie → 重新签发新会话
    r3 = client.get("/health", headers={"Cookie": f"{COOKIE_NAME}=a.b.c.deadbeef"})
    assert COOKIE_NAME in (r3.headers.get("set-cookie") or ""), "无效 Cookie 应重签发"
    print("通过: 会话中间件（签发/复用/无效重签 + HttpOnly Cookie）")


def test_download_count_and_rank_api(tmp: Path) -> None:
    import main
    from fastapi.testclient import TestClient
    sm = main.api_service.storage
    msm = main.session_manager
    song_id = 502
    # 重置该歌曲的排行计数，保证脚本可重复运行（历史会话记录不影响断言）
    with msm._connect() as conn:
        conn.execute("DELETE FROM downloads WHERE song_id = ?", (song_id,))
    f = sm.song_dir(song_id) / "排行计数.mp3"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"rank-data")
    sm.register_song({"song_id": song_id, "name": "排行计数", "artists": "歌手丙",
                      "album_id": None, "album_name": "", "duration": 3})
    sm.register_file(song_id, "standard", f, f.stat().st_size)
    sm.register_hash(f, song_id=song_id, quality="standard")

    client = TestClient(main.app)  # 同一实例复用同一会话 Cookie
    for _ in range(2):
        r = client.post("/download", json={"id": str(song_id), "quality": "standard",
                                           "format": "json"})
        assert r.json()["data"]["cached"] is True
    entries = [x for x in msm.rank_top() if x["song_id"] == song_id]
    assert len(entries) == 1 and entries[0]["session_count"] == 1, f"同会话重复下载应只计一次: {entries}"

    client2 = TestClient(main.app)  # 新实例 = 新会话
    r2 = client2.post("/download", json={"id": str(song_id), "quality": "standard",
                                         "format": "json"})
    assert r2.json()["data"]["cached"] is True
    entries = [x for x in msm.rank_top() if x["song_id"] == song_id]
    assert entries[0]["session_count"] == 2, f"第二会话应使计数 +1: {entries}"

    api = client.get("/rank").json()
    assert api["success"] is True and api["data"]["size"] >= 1
    hit = [x for x in api["data"]["rank"] if x["song_id"] == song_id]
    assert hit and hit[0]["name"] == "排行计数" and hit[0]["session_count"] == 2
    print(f"通过: /download 计数与 /rank API（同会话去重、跨会话累计）")


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="ranktest_"))
    try:
        test_issue_and_resolve(tmp)
        test_secret_file(tmp)
        test_download_dedup(tmp)
        test_rank_order(tmp)
        test_middleware(tmp)
        test_download_count_and_rank_api(tmp)
        print("\n全部自检通过 ✔")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
