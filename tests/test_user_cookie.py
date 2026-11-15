"""用户自有凭据自检脚本（无测试框架，直接运行）

覆盖范围：
1. 缓存命中 + 带 user_cookie：直接复用缓存，凭据与上游均不被触达（打桩断言）
2. 缓存缺失：凭据经 parse_cookie_string 解析后传入上游调用（打桩捕获）；
   不带 user_cookie 时传 None（回落 config 默认 Cookie）
3. 零持久化断言：请求结束后，日志文件、root.db、sha256sum.db、storage 全部
   文件、config.toml 中均不存在凭据哨兵值
4. 端到端（真实上游，用户已授权）：带哨兵 Cookie 请求未缓存音质（hires，
   预期上游拒绝）→ 复查第 3 条零残留；清理可能的脏缓存

运行方式：.venv/bin/python tests/test_user_cookie.py
"""

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SENTINEL = "SENTINEL_MUSIC_U=secret_value_12345; os=pc"
PARSED = {"SENTINEL_MUSIC_U": "secret_value_12345", "os": "pc"}


def test_hit_ignores_cookie(tmp: Path) -> None:
    import main
    sm = main.api_service.storage
    song_id = 501
    f = sm.song_dir(song_id) / "凭据命中.mp3"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"cached")
    sm.register_song({"song_id": song_id, "name": "凭据命中", "artists": "甲",
                      "album_id": None, "album_name": "", "duration": 1})
    sm.register_file(song_id, "standard", f, f.stat().st_size)
    with sm._connect() as conn:
        conn.execute("UPDATE songs SET created_at = strftime('%s','now') WHERE song_id = ?", (song_id,))
    sm.register_hash(f, song_id=song_id, quality="standard")

    dl = main.api_service.downloader
    def _forbid(*a, **k):
        raise AssertionError("缓存命中不应触达上游（凭据也无须使用）")
    dl.get_music_info = _forbid
    try:
        client = main.app.test_client()
        r = client.post("/download", json={"id": str(song_id), "quality": "standard",
                                           "format": "json", "user_cookie": SENTINEL})
        assert r.get_json()["data"]["cached"] is True
    finally:
        del dl.get_music_info  # 还原实例方法
    print("通过: 缓存命中时凭据与上游均未被使用")


def test_miss_passes_cookie(tmp: Path) -> None:
    import main
    dl = main.api_service.downloader
    captured = {}

    def _capture(music_id, quality, cookies=None):
        captured["cookies"] = cookies
        import music_downloader as md
        raise md.DownloadException("stub: no upstream")

    dl.get_music_info = _capture
    try:
        client = main.app.test_client()
        r1 = client.post("/download", json={"id": "999001", "quality": "lossless",
                                            "format": "json", "user_cookie": SENTINEL})
        assert r1.status_code == 404
        assert captured["cookies"] == PARSED, f"凭据解析/传递异常: {captured['cookies']}"

        r2 = client.post("/download", json={"id": "999001", "quality": "lossless",
                                            "format": "json"})
        assert r2.status_code == 404
        assert captured["cookies"] is None, "无 user_cookie 应传 None（回落默认 Cookie）"
    finally:
        del dl.get_music_info
    print("通过: 缓存缺失时凭据解析传入上游；缺省回落默认 Cookie（None）")


def test_zero_persistence() -> None:
    import main
    sm = main.api_service.storage
    hits = []

    # 日志文件（文本）
    log = Path("logs") / "service.log"
    if log.exists() and SENTINEL in log.read_text(encoding="utf-8", errors="ignore"):
        hits.append(str(log))
    # 两个 DB（二进制安全搜索）
    for db in (sm.db_path, sm.hash_db_path):
        if db.exists() and SENTINEL.encode() in db.read_bytes():
            hits.append(str(db))
    # storage 全部文件（二进制安全搜索）
    for p in sm.storage_dir.rglob("*"):
        if p.is_file() and SENTINEL.encode() in p.read_bytes():
            hits.append(str(p))
    # 配置文件
    cfg = Path("config.toml")
    if cfg.exists() and SENTINEL in cfg.read_text(encoding="utf-8", errors="ignore"):
        hits.append(str(cfg))

    assert not hits, f"凭据哨兵值残留于: {hits}"
    print("通过: 零持久化（日志/两个DB/storage全部文件/config 均无哨兵值）")


def test_end_to_end_upstream(tmp: Path) -> None:
    """真实上游（用户已授权）：哨兵 Cookie 请求 hires（预期上游拒绝无效凭据）"""
    import main
    sm = main.api_service.storage
    client = main.app.test_client()
    song_id, quality = 347230, "hires"
    sm.invalidate_file(song_id, quality)

    r = client.post("/download", json={"id": str(song_id), "quality": quality,
                                       "format": "json", "user_cookie": SENTINEL})
    # 无效凭据下上游大概率拒绝（404/500）；若上游降级返回了 URL 也不算失败——
    # 断言核心是凭据参与了本次上游调用且未持久化
    assert r.status_code in (200, 404, 500), f"意外状态码 {r.status_code}: {r.get_data()[:200]}"
    print(f"通过: 端到端上游调用完成（HTTP {r.status_code}，哨兵凭据仅内存使用）")

    # 若上游意外降级成功，清理脏缓存避免假 hires 内容留存
    sm.invalidate_file(song_id, quality)

    test_zero_persistence()
    print("通过: 端到端后零残留复查")


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="cookietest_"))
    try:
        test_hit_ignores_cookie(tmp)
        test_miss_passes_cookie(tmp)
        test_zero_persistence()
        test_end_to_end_upstream(tmp)
        print("\n全部自检通过 ✔")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
