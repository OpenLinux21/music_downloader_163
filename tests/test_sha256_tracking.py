"""哈希追踪与校验自检（pytest 管理）

覆盖范围：
1. compute_sha256 与 hashlib 单次计算一致
2. register_hash / verify_hash 回环；篡改、无记录、文件缺失三种失败分支
3. register_hash UPSERT：文件变化后重登记，verify_hash 恢复 True
4. invalidate_file：删除主文件+专辑副本、files 行与哈希记录，保留 songs 行与封面歌词哈希
5. 下载器集成：哈希一致 → cached=true 且不调 get_music_info（打桩断言）；
   哈希不一致 → invalidate + 走重新下载路径（get_music_info 打桩抛异常验证进入重下分支）
6. 清理联动：到期删除歌曲时哈希记录一并清除
7. 端到端（标记 upstream，默认跳过）：347230 lossless 未命中→登记哈希；
   二次请求命中校验通过；篡改 1 字节后重下并重新登记哈希

运行方式：python -m pytest tests/ -v
端到端上游用例须用户授权后显式运行：python -m pytest -m upstream
"""

import hashlib
import time
from pathlib import Path

import pytest

from storage_manager import StorageManager


def test_sha256_correctness(tmp: Path) -> None:
    sm = StorageManager(tmp / "s_hash")
    f = tmp / "data.bin"
    payload = b"hello-sha256" * 50000  # >1MB，覆盖分块读取路径
    f.write_bytes(payload)
    assert sm.compute_sha256(f) == hashlib.sha256(payload).hexdigest()


def test_register_verify(tmp: Path) -> None:
    sm = StorageManager(tmp / "s_hash")
    f = sm.song_dir(1) / "a.mp3"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"original-content")

    sm.register_hash(f, song_id=1, quality="standard")
    assert sm.verify_hash(f) is True

    # 无记录 → False
    other = sm.song_dir(1) / "b.mp3"
    other.write_bytes(b"x")
    assert sm.verify_hash(other) is False
    # 文件缺失 → False
    assert sm.verify_hash(sm.song_dir(1) / "ghost.mp3") is False

    # 篡改 → False；重登记（UPSERT）→ True
    f.write_bytes(b"tampered-content")
    assert sm.verify_hash(f) is False
    sm.register_hash(f, song_id=1, quality="standard")
    assert sm.verify_hash(f) is True


def test_invalidate(tmp: Path) -> None:
    sm = StorageManager(tmp / "s_invalidate")
    sm.register_song({"song_id": 2, "name": "失效测试", "artists": "甲", "album_id": 9,
                      "album_name": "专辑九", "duration": 10})
    main_f = sm.song_dir(2) / "song.mp3"
    main_f.parent.mkdir(parents=True)
    main_f.write_bytes(b"main")
    sm.register_file(2, "lossless", main_f, 4)
    sm.register_hash(main_f, song_id=2, quality="lossless")
    copy_f = sm.album_song_dir(9, 2) / "song.mp3"
    copy_f.parent.mkdir(parents=True)
    copy_f.write_bytes(b"main")
    sm.register_file(2, "lossless", copy_f, 4, is_copy=True)
    sm.register_hash(copy_f, song_id=2, quality="lossless")
    cover = sm.song_dir(2) / "cover.jpg"
    cover.write_bytes(b"cover")
    sm.register_hash(cover, song_id=2)

    sm.invalidate_file(2, "lossless")
    assert not main_f.exists() and not copy_f.exists()
    assert sm.get_cached_file(2, "lossless") is None
    assert sm.song_stats(2) is not None                      # songs 行保留（含访问量）
    assert sm.verify_hash(cover) is True                     # 封面哈希记录不受影响
    with sm._connect_hash() as conn:
        left = {r["path"] for r in conn.execute("SELECT path FROM file_hashes")}
    assert left == {sm._rel_path(cover)}


def test_downloader_integration(tmp: Path) -> None:
    from music_downloader import MusicDownloader

    dl = MusicDownloader(storage_dir=str(tmp / "s_dl"))
    calls = {"get_music_info": 0}

    def _forbid_upstream(*a, **k):
        calls["get_music_info"] += 1
        raise AssertionError("缓存命中不应调用 get_music_info（上游）")

    song_id = 3
    f = dl.storage.song_dir(song_id) / "集成哈希.mp3"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"valid-audio-bytes")
    dl.storage.register_song({"song_id": song_id, "name": "集成哈希", "artists": "乙",
                              "album_id": None, "album_name": "", "duration": 5})
    dl.storage.register_file(song_id, "standard", f, f.stat().st_size)
    dl.storage.register_hash(f, song_id=song_id, quality="standard")

    # 哈希一致：复用且不触上游
    dl.get_music_info = _forbid_upstream
    r = dl.download_music_file(song_id, "standard")
    assert r.success and r.cached and calls["get_music_info"] == 0

    # 篡改：校验失败 → 文件失效 → 进入重新下载分支（上游打桩抛 DownloadException）
    f.write_bytes(b"corrupted!")
    dl.get_music_info = lambda *a, **k: (_ for _ in ()).throw(
        __import__("music_downloader").DownloadException("stub: no upstream in test"))
    try:
        dl.download_music_file(song_id, "standard")
        raise AssertionError("篡改后应进入重新下载分支并触发打桩异常")
    except __import__("music_downloader").DownloadException as e:
        assert "stub" in str(e)
    assert not f.exists(), "损坏文件应已被删除"
    assert dl.storage.get_cached_file(song_id, "standard") is None


def test_cleanup_removes_hashes(tmp: Path) -> None:
    sm = StorageManager(tmp / "s_cleanup", retention_days=90)
    now = time.time()
    sm.register_song({"song_id": 4, "name": "将过期", "artists": "丙", "album_id": None,
                      "album_name": "", "duration": 1})
    with sm._connect() as conn:
        conn.execute("UPDATE songs SET created_at = ? WHERE song_id = 4", (now - 91 * 86400,))
    f = sm.song_dir(4) / "x.mp3"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"data")
    sm.register_file(4, "standard", f, 4)
    sm.register_hash(f, song_id=4, quality="standard")

    sm.cleanup(now=now)
    with sm._connect_hash() as conn:
        left = conn.execute("SELECT COUNT(*) AS c FROM file_hashes WHERE song_id = 4").fetchone()["c"]
    assert left == 0 and not f.exists()


@pytest.mark.upstream
def test_end_to_end() -> None:
    """真实上游端到端（须用户授权，-m upstream 显式运行）：
    未命中登记 → 命中校验 → 篡改重下 → 再命中"""
    import main
    sm = main.api_service.storage
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    song_id, quality = 347230, "lossless"

    # 若此前存在该音质缓存（如重跑本用例），清理以保证首次下载路径可测
    sm.invalidate_file(song_id, quality)

    r1 = client.post("/download", json={"id": str(song_id), "quality": quality, "format": "json"})
    d1 = r1.json()
    assert r1.status_code == 200 and d1["data"]["cached"] is False, d1
    main_path = Path(d1["data"]["file_path"])
    assert sm.verify_hash(main_path), "下载登记后哈希校验应通过"

    r2 = client.post("/download", json={"id": str(song_id), "quality": quality, "format": "json"})
    assert r2.json()["data"]["cached"] is True, "哈希一致应命中复用"

    # 篡改 1 字节 → 服务端校验失败 → 删除重下 → 重新登记哈希 → 再提供
    # 注意：read 后需回 seek 再写，确保目标字节必然翻转（否则可能写入相同值）
    with open(main_path, "r+b") as fh:
        fh.seek(1024)
        original = fh.read(1)
        fh.seek(1024)
        fh.write(b"\x00" if original != b"\x00" else b"\x01")
    r3 = client.post("/download", json={"id": str(song_id), "quality": quality, "format": "json"})
    d3 = r3.json()
    assert r3.status_code == 200 and d3["data"]["cached"] is False, d3
    assert sm.verify_hash(main_path), "重新下载后哈希应重新登记并通过"

    r4 = client.post("/download", json={"id": str(song_id), "quality": quality, "format": "json"})
    assert r4.json()["data"]["cached"] is True
