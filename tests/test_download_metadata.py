"""下载与元数据自检（pytest 管理）

覆盖范围：
1. 歌词嵌入实证（本地 ffmpeg/ffprobe，无上游）：mp3/flac/m4a 三种格式写入
   lyrics + 基础元数据后，用 ffprobe 验证各容器真实落tag情况
2. 限速器：_RateLimiter 平均速率收敛（200KB/s 传 400KB 约 2s）；不限速零等待
3. 多线程分段下载：本地支持 Range 的 HTTP 服务器（206）→ 4 段并发拼接结果与源一致
4. 回退单流：不支持 Range 的服务器（200）→ 自动回退，结果一致
5. 端到端（标记 upstream，默认跳过）：347230 exhigh 限速 512KB/s + 多线程下载，
   验证耗时下界、ffprobe 校验真实文件元数据/歌词/封面，路由缓存命中

运行方式：python -m pytest tests/ -v
端到端上游用例须用户授权后显式运行：python -m pytest -m upstream
"""

import json
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from music_downloader import MusicDownloader, MusicInfo, _RateLimiter


def _make_audio(dest: Path, codec_args: list) -> Path:
    subprocess.run(
        ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "anullsrc=r=44100:cl=mono", "-t", "1", *codec_args, str(dest)],
        check=True, capture_output=True,
    )
    return dest


def _probe_tags(path: Path) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format_tags:stream_tags",
         "-show_entries", "stream=codec_type", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    )
    data = json.loads(out.stdout)
    tags = {k.lower(): str(v) for k, v in data.get("format", {}).get("tags", {}).items()}
    stream_tags = {}
    for st in data.get("streams", []):
        stream_tags.update({k.lower(): str(v) for k, v in st.get("tags", {}).items()})
    tags["_streams"] = [s.get("codec_type") for s in data.get("streams", [])]
    tags["_stream_tags"] = stream_tags
    return tags


LYRIC = "[00:01.00]第一句歌词\n[00:02.00]第二句歌词"


def test_lyric_embedding(tmp: Path) -> None:
    dl = MusicDownloader(storage_dir=str(tmp / "storage"))
    cases = {
        "mp3": ["-q:a", "9"],
        "flac": ["-c:a", "flac"],
        "m4a": ["-c:a", "aac"],
    }
    info = MusicInfo(
        id=1, name="嵌入测试歌", artists="歌手甲", album="嵌入专辑", album_id=None,
        pic_url="", duration=60, track_number=3, download_url="", file_type="",
        file_size=0, quality="standard", lyric=LYRIC,
    )
    summary = []
    for ext, codec_args in cases.items():
        src = _make_audio(tmp / f"src.{ext}", codec_args)
        dest = tmp / f"tagged.{ext}"
        shutil.copy2(src, dest)
        ok = dl._write_metadata_with_ffmpeg(dest, info)
        tags = _probe_tags(dest)
        has_lyric = any(k.lower() == "lyrics" and str(v).strip() for k, v in tags.items() if k != "_streams")
        has_title = any(k.lower() == "title" for k, v in tags.items() if k != "_streams")
        # 基础元数据三格式必须全部成立；歌词按实测结果记录（mp3/flac/m4a 预期均支持）
        summary.append((ext, ok, has_lyric, has_title))

    for ext, write_ok, has_lyric, has_title in summary:
        assert write_ok, f"{ext} 元数据写入失败"
        assert has_title, f"{ext} 基础元数据写入失败"
        assert has_lyric, f"{ext} 歌词未嵌入（需核查 ffmpeg 行为）"


def test_rate_limiter() -> None:
    limiter = _RateLimiter(200)  # 200 KB/s
    chunk = 64 * 1024
    t0 = time.monotonic()
    for _ in range(6):  # 384KB → 期望 ~1.92s
        limiter.wait(chunk)
    dt = time.monotonic() - t0
    assert 1.4 <= dt <= 3.0, f"限速 200KB/s 传 384KB 耗时 {dt:.2f}s，超出容差"
    unlimited = _RateLimiter(0)
    t0 = time.monotonic()
    for _ in range(20):
        unlimited.wait(chunk)
    assert time.monotonic() - t0 < 0.05


class _RangeHandler(BaseHTTPRequestHandler):
    data = b""
    support_range = True

    def do_GET(self):
        rng = self.headers.get("Range") if self.support_range else None
        if rng and rng.startswith("bytes="):
            start_s, _, end_s = rng[6:].partition("-")
            start = int(start_s)
            end = min(int(end_s) if end_s else len(self.data) - 1, len(self.data) - 1)
            chunk = self.data[start:end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(self.data)}")
            self.send_header("Content-Length", str(len(chunk)))
            self.end_headers()
            self.wfile.write(chunk)
        else:
            self.send_response(200)
            self.send_header("Content-Length", str(len(self.data)))
            self.end_headers()
            self.wfile.write(self.data)

    def log_message(self, *args):
        pass


def _serve(data: bytes, support_range: bool):
    handler = type("H", (_RangeHandler,), {"data": data, "support_range": support_range})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/file"


def test_multi_thread_and_fallback(tmp: Path) -> None:
    dl = MusicDownloader(storage_dir=str(tmp / "storage"), multi_thread=True)
    payload = bytes(range(256)) * 8192  # 2MB 随机-ish 数据
    info = MusicInfo(
        id=2, name="传输测试", artists="歌手乙", album="专辑乙", album_id=None,
        pic_url="", duration=1, track_number=0, download_url="", file_type="mp3",
        file_size=len(payload), quality="standard",
    )

    # Range 服务器 → 4 段并发拼接
    server, url = _serve(payload, support_range=True)
    try:
        info.download_url = url
        dest = tmp / "multi.part"
        dl._download_to(url, dest, len(payload), _RateLimiter(0))
        assert dest.read_bytes() == payload, "多线程拼接结果不一致"
        assert not list(tmp.glob("multi.part.p*")), "分段临时文件残留"
    finally:
        server.shutdown()

    # 无 Range 服务器 → 自动回退单流
    server, url = _serve(payload, support_range=False)
    try:
        dest2 = tmp / "fallback.part"
        dl._download_to(url, dest2, len(payload), _RateLimiter(0))
        assert dest2.read_bytes() == payload, "回退单流结果不一致"
    finally:
        server.shutdown()


@pytest.mark.upstream
def test_end_to_end() -> None:
    """真实上游端到端（须用户授权，-m upstream 显式运行）：
    exhigh 限速 512KB/s + 多线程，验证耗时下界与元数据/歌词/封面/缓存命中"""
    import main
    sm = main.api_service.storage
    dl = MusicDownloader(
        storage_manager=sm, multi_thread=True, speed_limit_kb=512,
    )
    song_id, quality = 347230, "exhigh"
    sm.invalidate_file(song_id, quality)  # 保证走完整下载链路

    t0 = time.monotonic()
    result = dl.download_music_file(song_id, quality)
    dt = time.monotonic() - t0
    assert result.success and not result.cached, result.error_message
    size = result.file_size
    lower_bound = size / (512 * 1024) * 0.8  # 允许 20% 计时容差
    assert dt >= lower_bound, f"限速 512KB/s 下载 {size}B 仅耗时 {dt:.1f}s（下界 {lower_bound:.1f}s）"

    file_path = Path(result.file_path)
    tags = _probe_tags(file_path)
    # 全局元数据取 format_tags（封面流的 title=Album cover 属流级标记，不参与断言）
    tag_pairs = {k: v for k, v in tags.items() if not k.startswith("_")}
    assert tag_pairs.get("title") == "海阔天空", f"title 异常: {tag_pairs.get('title')}"
    assert "Beyond" in tag_pairs.get("artist", ""), f"artist 异常: {tag_pairs.get('artist')}"
    assert tag_pairs.get("album") == "海阔天空", f"album 异常: {tag_pairs.get('album')}"
    assert tag_pairs.get("lyrics", "").startswith("["), f"歌词未嵌入: {tag_pairs.get('lyrics')[:30]!r}"
    assert "video" in tags["_streams"], "封面流缺失"
    assert tags["_stream_tags"].get("comment") == "Cover (front)", "封面流标记缺失"

    # 路由层缓存命中（哈希校验通过）
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    r = client.post("/download", json={"id": str(song_id), "quality": quality, "format": "json"})
    assert r.json()["data"]["cached"] is True
