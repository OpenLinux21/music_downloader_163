"""存储与缓存自检脚本（无测试框架，直接运行）

覆盖范围（临时目录驱动，不发起任何上游请求）：
1. StorageManager 初始化：目录与 root.db 表结构创建
2. 歌曲登记与访问计数：重复登记保留 visit_count，record_access 正确累加
3. 音质文件路径解析：默认「歌曲名.ext」；扩展名被其他音质占用时加音质段
4. 缓存查询：(song_id, quality) 命中 / DB 缺记录 / 文件被删 / 专辑副本不作命中
5. 联查与音质列表：song_with_file、cached_qualities
6. 清理-到期：created_at 超过保留期 → 删除歌曲目录、专辑副本与 DB 行
7. 清理-超限：总大小超上限 → 按 visit_count 升序整歌淘汰至回到上限内
8. 下载器后处理集成：_post_register 完成登记/歌词落盘/元数据写入/专辑副本
   （音频由本地 ffmpeg 生成 1 秒静音 mp3，不消耗上游）
9. /download 路由缓存命中分支：format=json 返回 cached=true 且访问量 +1

运行方式：.venv/bin/python tests/test_storage_cache.py
"""

import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage_manager import StorageManager


def _make_mp3(dest: Path) -> Path:
    """用本地 ffmpeg 生成 1 秒静音 mp3（无上游消耗）"""
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "anullsrc=r=44100:cl=mono", "-t", "1", "-q:a", "9", str(dest)],
        check=True, capture_output=True,
    )
    return dest


def test_init(tmp: Path) -> None:
    sm = StorageManager(tmp / "storage")
    assert (tmp / "storage").is_dir() and sm.db_path.is_file()
    with sqlite3.connect(sm.db_path) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"songs", "files"} <= tables
    print("通过: storage 目录与 root.db 表结构初始化")


def test_register_and_access(tmp: Path) -> None:
    sm = StorageManager(tmp / "s_register")
    sm.register_song({"song_id": 1, "name": "歌一", "artists": "甲", "album_id": None,
                      "album_name": "", "duration": 180})
    sm.register_song({"song_id": 1, "name": "歌一", "artists": "甲", "album_id": None,
                      "album_name": "", "duration": 180})  # 重复登记
    sm.record_access(1)
    sm.record_access(1)
    stats = sm.song_stats(1)
    assert stats["visit_count"] == 2, f"访问量异常: {stats}"
    sm.record_access(99999)  # 未入库歌曲静默忽略
    assert sm.song_stats(99999) is None
    print("通过: 歌曲登记、重复登记保留访问量、record_access 累加")


def test_resolve_path(tmp: Path) -> None:
    sm = StorageManager(tmp / "s_resolve")
    p1 = sm.resolve_audio_path(2, "lossless", "歌名/含:非法", ".flac")
    assert p1 == sm.storage_dir / "2" / "歌名_含_非法.flac"
    # 登记 lossless 后，standard 解析得到独立的 mp3 路径并登记；
    # exhigh 请求同为 mp3 扩展名 → 已被 standard 占用 → 追加音质段
    sm.song_dir(2).mkdir(parents=True)
    (p1).write_bytes(b"x")
    sm.register_file(2, "lossless", p1, 1)
    p2 = sm.resolve_audio_path(2, "standard", "歌名/含:非法", ".mp3")
    assert p2.name == "歌名_含_非法.mp3"
    p2.write_bytes(b"x")
    sm.register_file(2, "standard", p2, 1)
    p3 = sm.resolve_audio_path(2, "exhigh", "歌名/含:非法", ".mp3")
    assert p3.name == "歌名_含_非法.exhigh.mp3", f"扩展名冲突未加音质段: {p3}"
    # 同音质再次解析保持幂等
    assert sm.resolve_audio_path(2, "standard", "歌名/含:非法", ".mp3") == p2
    print("通过: 命名规则（默认歌名.ext，扩展名冲突加音质段）")


def test_cache_queries(tmp: Path) -> None:
    sm = StorageManager(tmp / "s_cache")
    main_file = sm.song_dir(3) / "歌三.mp3"
    main_file.parent.mkdir(parents=True)
    main_file.write_bytes(b"data")
    sm.register_file(3, "standard", main_file, 4)
    assert sm.get_cached_file(3, "standard") == main_file

    # 专辑副本不作命中
    copy_path = sm.album_song_dir(55, 3) / "歌三.mp3"
    copy_path.parent.mkdir(parents=True)
    copy_path.write_bytes(b"data")
    sm.register_file(3, "standard", copy_path, 4, is_copy=True)  # 同键冲突？不同 is_copy 可共存
    assert sm.get_cached_file(3, "standard") == main_file

    # 文件被删（DB 残留）→ 不命中
    main_file.unlink()
    assert sm.get_cached_file(3, "standard") is None

    # DB 无记录 → 不命中
    other = sm.song_dir(3) / "流浪文件.mp3"
    other.write_bytes(b"x")
    assert sm.get_cached_file(3, "exhigh") is None

    # 联查与音质列表
    sm.register_song({"song_id": 3, "name": "歌三", "artists": "乙", "album_id": 55,
                      "album_name": "专辑三", "duration": 200})
    rec = sm.song_with_file(3, "standard")  # 主文件已删除但 DB 记录在
    assert rec and rec["name"] == "歌三" and rec["ext"] == ".mp3"
    qualities = sm.cached_qualities(3)
    assert {q["quality"] for q in qualities} == {"standard"}
    assert any(q["path"].startswith("albums/") for q in qualities)
    print("通过: 缓存命中判定（主存储/副本/文件缺失/无记录）与联查")


def test_cleanup_expired(tmp: Path) -> None:
    sm = StorageManager(tmp / "s_expired", retention_days=90)
    now = time.time()
    with sm._connect() as conn:
        conn.execute(
            "INSERT INTO songs (song_id, name, artists, album_id, album_name, duration,"
            " visit_count, created_at, last_access_at) VALUES (100, '老歌', '丙', 66, '老专辑',"
            " 100, 5, ?, ?)",
            (now - 91 * 86400, now - 91 * 86400),
        )
        conn.execute(
            "INSERT INTO songs (song_id, name, artists, album_id, album_name, duration,"
            " visit_count, created_at, last_access_at) VALUES (101, '新歌', '丙', NULL, '',"
            " 200, 5, ?, ?)",
            (now, now),
        )
    for sid, album in ((100, True), (101, False)):
        d = sm.song_dir(sid)
        d.mkdir(parents=True)
        (d / "audio.mp3").write_bytes(b"x")
        f = sm.song_dir(sid) / "audio.mp3"
        sm.register_file(sid, "standard", f, 10)
        if album:
            ad = sm.album_song_dir(66, sid)
            ad.mkdir(parents=True)
            (ad / "audio.mp3").write_bytes(b"x")
            sm.register_file(sid, "standard", ad / "audio.mp3", 10, is_copy=True)

    stats = sm.cleanup(now=now)
    assert stats["expired_removed"] == 1
    assert not sm.song_dir(100).exists()
    assert not (sm.albums_dir / "66" / "100").exists()
    assert sm.song_stats(100) is None
    assert sm.song_dir(101).exists() and sm.song_stats(101) is not None
    print("通过: 到期清理（歌曲目录+专辑副本+DB 行）")


def test_cleanup_evict(tmp: Path) -> None:
    sm = StorageManager(tmp / "s_evict", max_total_mb=1)  # 上限 1MB
    now = time.time()
    # 三首歌各登记 ~600KB：总量 1.8MB 超限；visit_count: 9<99<999 → 淘汰顺序可预期
    sizes = {200: 600 * 1024, 201: 600 * 1024, 202: 600 * 1024}
    visits = {200: 999, 201: 9, 202: 99}
    for sid, size in sizes.items():
        sm.register_song({"song_id": sid, "name": f"歌{sid}", "artists": "丁",
                          "album_id": None, "album_name": "", "duration": 1})
        with sm._connect() as conn:
            conn.execute("UPDATE songs SET visit_count = ? WHERE song_id = ?", (visits[sid], sid))
        f = sm.song_dir(sid) / "a.mp3"
        f.parent.mkdir(parents=True)
        f.write_bytes(b"\0" * size)
        sm.register_file(sid, "standard", f, size)

    stats = sm.cleanup(now=now)
    assert stats["evicted_removed"] == 2, f"应淘汰 2 首回到上限内: {stats}"
    assert sm.total_size() <= 1 * 1024 * 1024
    assert sm.song_stats(200) is not None      # 访问量最高者保留
    assert sm.song_stats(201) is None          # 访问量最低者先淘汰
    assert sm.song_stats(202) is None
    print("通过: 超限淘汰（按访问量升序整歌淘汰至上限内）")


def test_downloader_post_register(tmp: Path) -> None:
    from music_downloader import MusicDownloader, MusicInfo
    dl = MusicDownloader(storage_dir=str(tmp / "storage"))
    song_id, album_id = 300, 77
    audio = _make_mp3(tmp / "src.mp3")
    info = MusicInfo(
        id=song_id, name="集成测试歌", artists="戊/己", album="集成专辑",
        album_id=album_id, pic_url="", duration=60, track_number=3,
        download_url="", file_type="mp3", file_size=audio.stat().st_size,
        quality="standard", lyric="[00:01.00]测试歌词行",
    )
    target = dl._build_file_path(info)
    shutil.copy2(audio, target)
    dl._post_register(info, target)

    sm = dl.storage
    stats = sm.song_stats(song_id)
    assert stats and stats["album_id"] == album_id and stats["name"] == "集成测试歌"
    assert sm.get_cached_file(song_id, "standard") == target
    assert (sm.song_dir(song_id) / "lyric.lrc").read_text(encoding="utf-8").startswith("[00:01.00]")
    copy_dir = sm.album_song_dir(album_id, song_id)
    assert (copy_dir / target.name).is_file()
    assert (copy_dir / "lyric.lrc").is_file()
    assert sm.total_size() == target.stat().st_size * 2
    print("通过: _post_register 登记/歌词落盘/元数据写入/专辑副本")


def test_route_cache_hit(tmp: Path) -> None:
    import main
    # 向服务的存储管理器注入一首已缓存歌曲（真实 storage 目录，先重置该歌曲状态保证可重复运行）
    sm = main.api_service.storage
    song_id = 400
    with sm._connect() as conn:
        conn.execute("DELETE FROM files WHERE song_id = ?", (song_id,))
        conn.execute("DELETE FROM songs WHERE song_id = ?", (song_id,))
    shutil.rmtree(sm.song_dir(song_id), ignore_errors=True)
    shutil.rmtree(sm.albums_dir / "400", ignore_errors=True)

    f = sm.song_dir(song_id) / "缓存路由测试.mp3"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"cached-audio")
    sm.register_song({"song_id": song_id, "name": "缓存路由测试", "artists": "庚",
                      "album_id": None, "album_name": "", "duration": 30})
    sm.register_file(song_id, "lossless", f, f.stat().st_size)
    before = sm.song_stats(song_id)["visit_count"]

    client = main.app.test_client()
    resp = client.post("/download", json={"id": str(song_id), "quality": "lossless",
                                          "format": "json"})
    data = resp.get_json()
    assert resp.status_code == 200 and data["success"], data
    assert data["data"]["cached"] is True and data["data"]["name"] == "缓存路由测试"
    assert sm.song_stats(song_id)["visit_count"] == before + 1
    print("通过: /download 缓存命中分支（cached=true 且访问量 +1，未发起上游调用）")


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="storagetest_"))
    try:
        test_init(tmp)
        test_register_and_access(tmp)
        test_resolve_path(tmp)
        test_cache_queries(tmp)
        test_cleanup_expired(tmp)
        test_cleanup_evict(tmp)
        test_downloader_post_register(tmp)
        test_route_cache_hit(tmp)
        print("\n全部自检通过 ✔")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
