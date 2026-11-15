"""网易云音乐下载器（适配 ffmpeg 写入元数据）

提供功能：
- 同步 / 异步下载（本地落盘 / 内存返回）
- 批量并发下载
- 进度查询
- 基于 ffmpeg 的元数据 + 封面 + 歌词嵌入（MP3/FLAC/M4A/MP4/OGG/Opus）
- 传输层：单流 / 4 段 Range 多线程（自动回退）+ KB/s 限速（同步路径）

设计要点：
1. ffmpeg 路径懒加载：每次写入前重新检测，支持服务启动后中途安装
2. 显式 -f 指定输出格式 + 临时文件保留原扩展名在最后，双保险
3. MP4（杜比全景声 EAC3）单独分支，启用 +faststart
4. 扩展名优先使用 API 返回的 type 字段，URL/Content-Type 兜底
5. 歌词经 -metadata lyrics= 嵌入容器（mp3=USLT / flac=LYRICS / m4a=©lyr）
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from enum import Enum
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiofiles
import aiohttp
import requests

from music_api import APIException, NeteaseAPI, load_cookies
from logger import get_logger
from storage_manager import StorageManager


# ============ 类型定义 ============

class AudioFormat(Enum):
    """音频格式枚举"""
    MP3 = "mp3"
    FLAC = "flac"
    M4A = "m4a"
    MP4 = "mp4"
    UNKNOWN = "unknown"


class QualityLevel(Enum):
    """音质等级枚举"""
    STANDARD = "standard"
    EXHIGH = "exhigh"
    LOSSLESS = "lossless"
    HIRES = "hires"
    SKY = "sky"
    JYEFFECT = "jyeffect"
    JYMASTER = "jymaster"
    DOLBY = "dolby"


@dataclass
class MusicInfo:
    """音乐信息数据类"""
    id: int
    name: str
    artists: str
    album: str
    album_id: Optional[int]
    pic_url: str
    duration: int
    track_number: int
    download_url: str
    file_type: str
    file_size: int
    quality: str
    lyric: str = ""
    tlyric: str = ""


@dataclass
class DownloadResult:
    """下载结果数据类"""
    success: bool
    file_path: Optional[str] = None
    file_size: int = 0
    error_message: str = ""
    music_info: Optional[MusicInfo] = None
    cached: bool = False  # 是否命中缓存复用


class DownloadException(Exception):
    """下载异常类"""
    pass


class _RateLimiter:
    """简单限速器：按平均速率控制字节写入节奏（多线程共享实例）

    rate_kb 为 0 时不限速。实现为「按已发字节计算应达时刻」的均匀节流，
    长时间平均速率收敛于目标值（非严格令牌桶，够用且无突发）。
    """

    def __init__(self, rate_kb: float):
        self.rate = float(rate_kb) * 1024  # 字节/秒
        self._lock = threading.Lock()
        self._sent = 0.0
        self._t0 = time.monotonic()

    def wait(self, n_bytes: int) -> None:
        if self.rate <= 0 or n_bytes <= 0:
            return
        with self._lock:
            self._sent += n_bytes
            target = self._t0 + self._sent / self.rate
        delay = target - time.monotonic()
        if delay > 0:
            time.sleep(delay)


# ============ 工具映射表 ============

# API 返回的 type -> 文件扩展名
API_TYPE_TO_EXT = {
    "mp3": ".mp3",
    "flac": ".flac",
    "m4a": ".m4a",
    "mp4": ".mp4",
    "ogg": ".ogg",
    "opus": ".opus",
}

# 文件扩展名 -> ffmpeg 输出 muxer 名称
EXT_TO_FFMPEG_FORMAT = {
    ".mp3": "mp3",
    ".flac": "flac",
    ".m4a": "mp4",
    ".mp4": "mp4",
    ".ogg": "ogg",
    ".opus": "opus",
}

# 支持元数据写入的扩展名
METADATA_FORMATS = {".mp3", ".flac", ".m4a", ".mp4", ".ogg", ".opus"}


# ============ 核心实现 ============

class MusicDownloader:
    """网易云音乐下载器

    用法:
        downloader = MusicDownloader(storage_dir="storage")
        result = downloader.download_music_file(music_id, quality="lossless")
    """

    def __init__(
        self,
        storage_dir: str = "storage",
        max_concurrent: int = 3,
        ffmpeg_path: Optional[str] = None,
        storage_manager: Optional[StorageManager] = None,
        multi_thread: bool = False,
        speed_limit_kb: float = 0,
    ):
        """
        Args:
            storage_dir: 存储根目录（按歌曲 ID 分类；测试时可指向临时目录）
            max_concurrent: 最大并发下载数
            ffmpeg_path: ffmpeg 可执行文件路径，为 None 时启动时检测一次
            storage_manager: 外部注入的存储管理器（服务侧传入带配置的实例）；
                缺省时按 storage_dir 自建（不限总量、默认保留期）
            multi_thread: 单文件多线程（Range 分段）下载开关；服务端不支持 Range 时自动回退单流
            speed_limit_kb: 下载限速（KB/s），0 表示不限速
        """
        self.logger = get_logger("DOWNLOADER")
        self.storage = storage_manager or StorageManager(storage_dir)
        self.max_concurrent = max_concurrent
        self.multi_thread = bool(multi_thread)
        self.speed_limit_kb = float(speed_limit_kb)

        self.api = NeteaseAPI()

        # ffmpeg 路径缓存：启动时检测一次，写入时还会再懒加载一次
        self.ffmpeg_path = ffmpeg_path or shutil.which("ffmpeg")
        if not self.ffmpeg_path:
            self.logger.warning("未检测到 ffmpeg，写入元数据功能将不可用")

    # ----------- 工具方法 -----------

    def _sanitize_filename(self, filename: str) -> str:
        """清理文件名非法字符"""
        filename = re.sub(r'[<>:"/\\|?*]', '_', filename)
        filename = filename.strip(' .')
        if len(filename) > 200:
            filename = filename[:200]
        return filename or "unknown"

    def _determine_file_extension(
        self,
        url: str,
        content_type: str = "",
        api_type: str = "",
    ) -> str:
        """确定文件扩展名

        优先级: API 返回的 type > URL 后缀 > HTTP Content-Type > 默认 mp3
        """
        api_type = (api_type or "").lower()
        if api_type in API_TYPE_TO_EXT:
            return API_TYPE_TO_EXT[api_type]

        url_lower = url.lower()
        if ".flac" in url_lower:
            return ".flac"
        if ".mp3" in url_lower:
            return ".mp3"
        if ".m4a" in url_lower or ".mp4" in url_lower:
            return ".m4a"

        content_type = (content_type or "").lower()
        if "flac" in content_type:
            return ".flac"
        if "mpeg" in content_type or "mp3" in content_type:
            return ".mp3"
        if "mp4" in content_type or "m4a" in content_type:
            return ".m4a"

        return ".mp3"

    def _resolve_ffmpeg(self) -> Optional[str]:
        """懒加载解析 ffmpeg 路径

        每次写入前调用一次：若启动时未检测到，但中途已安装，下一次就能用。
        """
        if self.ffmpeg_path:
            return self.ffmpeg_path
        path = shutil.which("ffmpeg")
        if path:
            self.ffmpeg_path = path  # 回写缓存
        return path

    # ----------- 信息获取 -----------

    def get_music_info(self, music_id: int, quality: str = "standard") -> MusicInfo:
        """获取音乐详细信息（同步）"""
        try:
            cookies = load_cookies()

            url_result = self.api.get_song_url(music_id, quality, cookies)
            if not url_result.get("data") or not url_result["data"]:
                raise DownloadException(f"无法获取音乐ID {music_id} 的播放链接")

            song_data = url_result["data"][0]
            download_url = song_data.get("url", "")
            if not download_url:
                raise DownloadException(f"音乐ID {music_id} 无可用的下载链接")

            detail_result = self.api.get_song_detail(music_id)
            if not detail_result.get("songs") or not detail_result["songs"]:
                raise DownloadException(f"无法获取音乐ID {music_id} 的详细信息")

            song_detail = detail_result["songs"][0]

            lyric_result = self.api.get_lyric(music_id, cookies)
            lyric = lyric_result.get("lrc", {}).get("lyric", "") if lyric_result else ""
            tlyric = lyric_result.get("tlyric", {}).get("lyric", "") if lyric_result else ""

            artists = "/".join(a["name"] for a in song_detail.get("ar", []))

            return MusicInfo(
                id=music_id,
                name=song_detail.get("name", "未知歌曲"),
                artists=artists or "未知艺术家",
                album=song_detail.get("al", {}).get("name", "未知专辑"),
                album_id=song_detail.get("al", {}).get("id"),
                pic_url=song_detail.get("al", {}).get("picUrl", ""),
                duration=song_detail.get("dt", 0) // 1000,
                track_number=song_detail.get("no", 0),
                download_url=download_url,
                file_type=song_data.get("type", "mp3").lower(),
                file_size=song_data.get("size", 0),
                quality=quality,
                lyric=lyric,
                tlyric=tlyric,
            )

        except APIException as e:
            raise DownloadException(f"API调用失败: {e}")
        except Exception as e:
            raise DownloadException(f"获取音乐信息时发生错误: {e}")

    # ----------- 文件路径 -----------

    def _build_file_path(self, music_info: MusicInfo) -> Path:
        """根据 MusicInfo 确定存储路径：storage/<song_id>/歌曲名[.quality].<ext>"""
        ext = self._determine_file_extension(
            music_info.download_url,
            api_type=music_info.file_type,
        )
        path = self.storage.resolve_audio_path(
            music_info.id, music_info.quality, music_info.name, ext
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    # ----------- 传输层（单流 / 多线程分段 + 限速） -----------

    def _download_single_stream(self, url: str, dest: Path, limiter: _RateLimiter) -> int:
        """单流下载到 dest，返回写入字节数"""
        written = 0
        with requests.get(url, stream=True, timeout=30) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        f.write(chunk)
                        written += len(chunk)
                        limiter.wait(len(chunk))
        return written

    def _download_ranged(self, url: str, start: int, end: int, dest: Path,
                         limiter: _RateLimiter) -> int:
        """Range 分段下载 [start, end] 到 dest，返回写入字节数"""
        written = 0
        headers = {"Range": f"bytes={start}-{end}"}
        with requests.get(url, stream=True, timeout=30, headers=headers) as resp:
            resp.raise_for_status()
            if resp.status_code != 206:
                raise RuntimeError(f"服务端不支持 Range 请求（HTTP {resp.status_code}）")
            with open(dest, "wb") as f:
                for chunk in resp.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        f.write(chunk)
                        written += len(chunk)
                        limiter.wait(len(chunk))
        return written

    def _download_to(self, url: str, dest: Path, total_size: int,
                     limiter: _RateLimiter) -> None:
        """按配置执行单流或多线程分段下载，写入 dest（.part 中间文件）

        多线程：按 4 段 Range 并发拉取后顺序拼接；总大小未知或任一分段失败时
        自动回退单流（失败的分段临时文件会被清理）。
        """
        threads = 4
        use_multi = (
            self.multi_thread
            and total_size and total_size > 512 * 1024  # 小文件不值得分段
        )
        if use_multi:
            part_size = total_size // threads
            ranges: List[Tuple[int, int]] = []
            for i in range(threads):
                start = i * part_size
                end = (total_size - 1) if i == threads - 1 else (start + part_size - 1)
                ranges.append((start, end))
            part_files = [dest.with_name(dest.name + f".p{i}") for i in range(threads)]
            try:
                with ThreadPoolExecutor(max_workers=threads) as pool:
                    futures = [
                        pool.submit(self._download_ranged, url, s, e, pf, limiter)
                        for (s, e), pf in zip(ranges, part_files)
                    ]
                    sizes = [fut.result() for fut in futures]
                if sum(sizes) != total_size:
                    raise RuntimeError(
                        f"分段大小校验失败: 拼接 {sum(sizes)} != 期望 {total_size}"
                    )
                with open(dest, "wb") as out:
                    for pf in part_files:
                        with open(pf, "rb") as src:
                            shutil.copyfileobj(src, out)
                for pf in part_files:
                    pf.unlink(missing_ok=True)
                return
            except Exception as e:
                self.logger.warning(f"多线程下载失败，回退单流: {e}")
                for pf in part_files:
                    pf.unlink(missing_ok=True)
        self._download_single_stream(url, dest, limiter)

    # ----------- 下载（同步） -----------

    def download_music_file(
        self,
        music_id: int,
        quality: str = "standard",
    ) -> DownloadResult:
        """同步下载音乐到本地存储（命中缓存直接复用，不重复下载）"""
        try:
            music_id = int(music_id)

            # 缓存命中：先校验哈希，一致才复用（访问计数由调用方累加）
            cached = self.storage.get_cached_file(music_id, quality)
            if cached is not None and self.storage.verify_hash(cached):
                record = self.storage.song_with_file(music_id, quality)
                if record is not None:
                    return DownloadResult(
                        success=True,
                        file_path=str(cached),
                        file_size=cached.stat().st_size,
                        music_info=self._music_info_from_record(record, quality),
                        cached=True,
                    )
            if cached is not None:
                self.logger.warning(f"缓存文件哈希校验未通过，删除并重新下载: {cached}")
                self.storage.invalidate_file(music_id, quality)

            music_info = self.get_music_info(music_id, quality)
            file_path = self._build_file_path(music_info)

            # 下载到临时文件（按配置单流/多线程 + 限速），完成后原子改名
            tmp_path = file_path.with_name(file_path.name + ".part")
            self._download_to(music_info.download_url, tmp_path,
                              music_info.file_size, _RateLimiter(self.speed_limit_kb))
            tmp_path.replace(file_path)

            # 登记 + 后处理（封面/歌词落盘、元数据写入、专辑副本）
            self._post_register(music_info, file_path)

            return DownloadResult(
                success=True,
                file_path=str(file_path),
                file_size=file_path.stat().st_size,
                music_info=music_info,
            )

        except DownloadException:
            raise
        except requests.RequestException as e:
            return DownloadResult(success=False, error_message=f"下载请求失败: {e}")
        except Exception as e:
            return DownloadResult(success=False, error_message=f"下载过程中发生错误: {e}")

    # ----------- 下载后登记与后处理 -----------

    def _post_register(self, music_info: MusicInfo, file_path: Path) -> None:
        """下载完成后的统一后处理：封面/歌词落盘、ffmpeg 元数据、登记 DB、专辑副本

        登记放在元数据写入之后，保证 DB 记录的文件大小与最终落盘一致。
        """
        storage = self.storage
        storage.register_song({
            "song_id": music_info.id,
            "name": music_info.name,
            "artists": music_info.artists,
            "album_id": music_info.album_id,
            "album_name": music_info.album,
            "duration": music_info.duration,
        })

        # 封面下载一次：既落盘 storage/<song_id>/cover.jpg，也供 ffmpeg 嵌入复用
        cover_local: Optional[Path] = None
        if music_info.pic_url:
            try:
                pic_resp = requests.get(music_info.pic_url, timeout=10)
                pic_resp.raise_for_status()
                cover_local = storage.save_cover(music_info.id, pic_resp.content)
                storage.register_hash(cover_local, song_id=music_info.id)
            except Exception as e:
                self.logger.warning(f"封面下载失败，跳过封面保存: {e}")

        lyric_path = storage.save_lyric(music_info.id, music_info.lyric)
        if lyric_path:
            storage.register_hash(lyric_path, song_id=music_info.id)

        self._write_metadata_with_ffmpeg(file_path, music_info, cover_local=cover_local)

        storage.register_file(music_info.id, music_info.quality, file_path,
                              file_path.stat().st_size, is_copy=False)
        # 哈希登记：任何文件进入 storage 都计算并登记 SHA-256
        storage.register_hash(file_path, song_id=music_info.id, quality=music_info.quality)

        # 归属专辑：另存副本到 storage/albums/<album_id>/<song_id>/（含封面歌词）
        if music_info.album_id:
            try:
                album_dir = storage.album_song_dir(music_info.album_id, music_info.id)
                album_dir.mkdir(parents=True, exist_ok=True)
                copy_path = album_dir / file_path.name
                shutil.copy2(file_path, copy_path)
                storage.register_file(music_info.id, music_info.quality, copy_path,
                                      copy_path.stat().st_size, is_copy=True)
                storage.register_hash(copy_path, song_id=music_info.id,
                                      quality=music_info.quality)
                if cover_local and not (album_dir / "cover.jpg").exists():
                    cover_copy = album_dir / "cover.jpg"
                    shutil.copy2(cover_local, cover_copy)
                    storage.register_hash(cover_copy, song_id=music_info.id)
                lyric_src = storage.song_dir(music_info.id) / "lyric.lrc"
                if lyric_src.exists() and not (album_dir / "lyric.lrc").exists():
                    lyric_copy = album_dir / "lyric.lrc"
                    shutil.copy2(lyric_src, lyric_copy)
                    storage.register_hash(lyric_copy, song_id=music_info.id)
            except Exception as e:
                self.logger.warning(f"专辑副本保存失败（不影响主文件）: {e}")

    def _music_info_from_record(self, record: Dict[str, Any], quality: str) -> MusicInfo:
        """用 root.db 记录构建 MusicInfo（缓存命中时避免重复上游调用）"""
        return MusicInfo(
            id=int(record["song_id"]),
            name=record.get("name", ""),
            artists=record.get("artists", ""),
            album=record.get("album_name", ""),
            album_id=record.get("album_id"),
            pic_url="",
            duration=int(record.get("duration", 0)),
            track_number=0,
            download_url="",
            file_type=str(record.get("ext", ".mp3")).lstrip("."),
            file_size=int(record.get("file_size", 0)),
            quality=quality,
        )

    # ----------- 下载（异步） -----------

    async def download_music_file_async(
        self,
        music_id: int,
        quality: str = "standard",
    ) -> DownloadResult:
        """异步下载音乐到本地存储（命中缓存直接复用）"""
        try:
            music_id = int(music_id)

            cached = self.storage.get_cached_file(music_id, quality)
            if cached is not None and self.storage.verify_hash(cached):
                record = self.storage.song_with_file(music_id, quality)
                if record is not None:
                    return DownloadResult(
                        success=True,
                        file_path=str(cached),
                        file_size=cached.stat().st_size,
                        music_info=self._music_info_from_record(record, quality),
                        cached=True,
                    )
            if cached is not None:
                self.logger.warning(f"缓存文件哈希校验未通过，删除并重新下载: {cached}")
                self.storage.invalidate_file(music_id, quality)

            music_info = self.get_music_info(music_id, quality)
            file_path = self._build_file_path(music_info)

            tmp_path = file_path.with_name(file_path.name + ".part")
            async with aiohttp.ClientSession() as session:
                async with session.get(music_info.download_url) as resp:
                    resp.raise_for_status()
                    async with aiofiles.open(tmp_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(8192):
                            await f.write(chunk)
            tmp_path.replace(file_path)

            self._post_register(music_info, file_path)

            return DownloadResult(
                success=True,
                file_path=str(file_path),
                file_size=file_path.stat().st_size,
                music_info=music_info,
            )

        except DownloadException:
            raise
        except aiohttp.ClientError as e:
            return DownloadResult(success=False, error_message=f"异步下载请求失败: {e}")
        except Exception as e:
            return DownloadResult(success=False, error_message=f"异步下载过程中发生错误: {e}")

    # ----------- 下载到内存 -----------

    def download_music_to_memory(
        self,
        music_id: int,
        quality: str = "standard",
    ) -> Tuple[bool, BytesIO, MusicInfo]:
        """下载音乐到内存"""
        try:
            music_info = self.get_music_info(music_id, quality)
            resp = requests.get(music_info.download_url, timeout=30)
            resp.raise_for_status()
            return True, BytesIO(resp.content), music_info

        except DownloadException:
            raise
        except requests.RequestException as e:
            raise DownloadException(f"下载到内存失败: {e}")
        except Exception as e:
            raise DownloadException(f"内存下载过程中发生错误: {e}")

    # ----------- 批量下载 -----------

    async def download_batch_async(
        self,
        music_ids: List[int],
        quality: str = "standard",
    ) -> List[DownloadResult]:
        """批量并发下载"""
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def _one(music_id: int) -> DownloadResult:
            async with semaphore:
                return await self.download_music_file_async(music_id, quality)

        tasks = [_one(mid) for mid in music_ids]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        processed: List[DownloadResult] = []
        for i, r in enumerate(results):
            if isinstance(r, Exception):
                processed.append(DownloadResult(
                    success=False,
                    error_message=f"下载音乐ID {music_ids[i]} 时发生异常: {r}",
                ))
            else:
                processed.append(r)
        return processed

    # ----------- 进度查询 -----------

    def get_download_progress(
        self,
        music_id: int,
        quality: str = "standard",
    ) -> Dict[str, Any]:
        """查询下载进度"""
        try:
            music_info = self.get_music_info(music_id, quality)
            file_path = self._build_file_path(music_info)

            if file_path.exists():
                current_size = file_path.stat().st_size
                progress = (
                    current_size / music_info.file_size * 100
                    if music_info.file_size > 0
                    else 0
                )
                return {
                    "music_id": music_id,
                    "filename": file_path.name,
                    "total_size": music_info.file_size,
                    "current_size": current_size,
                    "progress": min(progress, 100),
                    "completed": current_size >= music_info.file_size,
                }

            return {
                "music_id": music_id,
                "filename": file_path.name,
                "total_size": music_info.file_size,
                "current_size": 0,
                "progress": 0,
                "completed": False,
            }

        except Exception as e:
            return {
                "music_id": music_id,
                "error": str(e),
                "progress": 0,
                "completed": False,
            }

    # ============ ffmpeg 元数据写入（核心） ============

    def _write_metadata_with_ffmpeg(
        self,
        file_path: Path,
        music_info: MusicInfo,
        cover_local: Optional[Path] = None,
    ) -> bool:
        """使用 ffmpeg 写入音频元数据

        处理: title/artist/album/track + 封面（如果是 mp3/flac/m4a/mp4）
        cover_local: 已落盘的封面文件（storage/<song_id>/cover.jpg），提供时不再重复下载

        Returns:
            是否写入成功（失败不影响主流程）
        """
        ffmpeg_path = self._resolve_ffmpeg()
        if not ffmpeg_path:
            self.logger.warning("跳过元数据写入: ffmpeg 不可用")
            return False

        file_ext = file_path.suffix.lower()
        if file_ext not in METADATA_FORMATS:
            self.logger.warning(f"跳过元数据写入: 不支持的格式 {file_ext}")
            return False

        if not file_path.exists():
            self.logger.warning(f"跳过元数据写入: 文件不存在 {file_path}")
            return False

        # 临时输出文件：保留原扩展名在最后，便于 ffmpeg 识别 muxer
        # 例: 艾辰 - 错位时空.tagging.flac
        tmp_output = file_path.with_name(
            file_path.stem + ".tagging" + file_path.suffix
        )
        # 临时封面文件
        cover_tmp: Optional[str] = None

        try:
            # 封面来源：优先使用已落盘文件；无则下载到临时文件
            if cover_local is not None and cover_local.exists():
                cover_tmp = str(cover_local)
            elif music_info.pic_url:
                try:
                    pic_resp = requests.get(music_info.pic_url, timeout=10)
                    pic_resp.raise_for_status()
                    fd, cover_tmp = tempfile.mkstemp(suffix=".jpg", dir=str(self.storage.storage_dir))
                    os.close(fd)
                    with open(cover_tmp, "wb") as cf:
                        cf.write(pic_resp.content)
                except Exception as e:
                    self.logger.warning(f"封面下载失败，跳过封面写入: {e}")
                    cover_tmp = None

            # 构造 ffmpeg 命令
            # 顺序: global opts -> -i input1 -> -i input2 -> metadata -> mapping -> -f format -> output
            output_format = EXT_TO_FFMPEG_FORMAT.get(file_ext, "")
            cmd: List[str] = [
                ffmpeg_path, "-y", "-loglevel", "error",
                "-i", str(file_path),
            ]
            if cover_tmp:
                cmd += ["-i", cover_tmp]

            # 通用元数据（lyrics 嵌入歌词原文，使文件独立携带歌词，无需外部 .lrc）
            meta = {
                "title": music_info.name,
                "artist": music_info.artists,
                "album": music_info.album,
                "album_artist": music_info.artists,
                "comment": "Downloaded from Netease Cloud Music",
            }
            if music_info.lyric and music_info.lyric.strip():
                meta["lyrics"] = music_info.lyric
            if music_info.track_number > 0:
                meta["track"] = str(music_info.track_number)
                meta["tracktotal"] = str(music_info.track_number)
            for k, v in meta.items():
                if v:
                    cmd += ["-metadata", f"{k}={v}"]

            # 各格式的 mapping / 编码选项
            cmd += self._build_format_args(file_ext, has_cover=bool(cover_tmp))

            # -f 必须放在所有 -i 之后（作为 output option）才生效
            if output_format:
                cmd += ["-f", output_format]
            cmd.append(str(tmp_output))

            # 执行
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120,
                encoding="utf-8",
                errors="ignore",
            )

            if result.returncode == 0 and tmp_output.exists():
                # 原子替换原文件
                shutil.move(str(tmp_output), str(file_path))
                self.logger.info(f"元数据写入完成: {file_path.name}")
                return True

            self.logger.error(
                f"ffmpeg 写入元数据失败: "
                f"{(result.stderr or result.stdout or '').strip()}"
            )
            return False

        except subprocess.TimeoutExpired:
            self.logger.error("ffmpeg 执行超时")
            return False
        except FileNotFoundError:
            self.logger.error(f"ffmpeg 可执行文件未找到: {ffmpeg_path}")
            return False
        except Exception as e:
            self.logger.error(f"ffmpeg 写入元数据异常: {e}")
            return False
        finally:
            # 清理封面临时文件（已持久化的 cover.jpg 不删）
            if cover_tmp and cover_tmp != str(cover_local or "") and os.path.exists(cover_tmp):
                try:
                    os.unlink(cover_tmp)
                except OSError:
                    pass
            # 清理可能残留的临时输出
            if tmp_output.exists():
                try:
                    tmp_output.unlink()
                except OSError:
                    pass

    def _build_format_args(self, file_ext: str, has_cover: bool) -> List[str]:
        """根据文件格式构造 mapping / 编码参数"""
        if file_ext == ".mp3":
            # MP3: 封面通过 ID3v2 APIC 帧写入
            if has_cover:
                return [
                    "-map", "0:a", "-map", "1:v",
                    "-c:a", "copy", "-c:v", "copy",
                    "-id3v2_version", "3",
                    "-metadata:s:v", "title=Album cover",
                    "-metadata:s:v", "comment=Cover (front)",
                ]
            return ["-map", "0:a", "-c:a", "copy", "-id3v2_version", "3"]

        if file_ext == ".flac":
            # FLAC: 原生支持封面
            if has_cover:
                return [
                    "-map", "0", "-map", "1",
                    "-c", "copy",
                    "-disposition:v:0", "attached_pic",
                ]
            return ["-map", "0", "-c", "copy"]

        if file_ext == ".m4a":
            # M4A: AAC 音频 + mp4 容器
            if has_cover:
                return [
                    "-map", "0:a", "-map", "1:v",
                    "-c", "copy",
                    "-disposition:v:0", "attached_pic",
                    "-metadata:s:v", "title=Album cover",
                    "-metadata:s:v", "comment=Cover (front)",
                ]
            return ["-map", "0:a", "-c:a", "copy"]

        if file_ext == ".mp4":
            # MP4 (杜比全景声 EAC3 / 其它 mp4 容器音频)
            # 杜比文件通常无视频流，封面作为 video stream 注入；+faststart 让 moov 在文件头
            if has_cover:
                return [
                    "-map", "0", "-map", "1:v",
                    "-c", "copy",
                    "-disposition:v:0", "attached_pic",
                    "-metadata:s:v", "title=Album cover",
                    "-metadata:s:v", "comment=Cover (front)",
                    "-movflags", "+faststart",
                ]
            return ["-map", "0", "-c", "copy", "-movflags", "+faststart"]

        if file_ext in (".ogg", ".opus"):
            if has_cover:
                return [
                    "-map", "0:a", "-map", "1:v",
                    "-c:a", "copy", "-c:v", "copy",
                ]
            return ["-map", "0:a", "-c:a", "copy"]

        # 默认
        if has_cover:
            return ["-map", "0:a", "-map", "1:v", "-c", "copy"]
        return ["-map", "0:a", "-c:a", "copy"]


# ============ CLI 入口 ============

if __name__ == "__main__":
    import sys
    from dataclasses import asdict

    downloader = MusicDownloader()

    # 无参数：打印模块信息
    if len(sys.argv) < 2:
        print("网易云音乐下载器")
        print(f"ffmpeg 路径: {downloader.ffmpeg_path or '未找到'}")
        print("支持的音质: standard / exhigh / lossless / hires / sky / jyeffect / jymaster / dolby")
        print("支持的功能: 同步/异步/批量下载、内存下载、进度查询、ffmpeg 元数据写入")
        print()
        print("用法:")
        print("  python music_downloader.py <music_id> [quality]            # 仅打印信息")
        print("  python music_downloader.py <music_id> [quality] --download # 打印并下载")
        print()
        print("示例:")
        print("  python music_downloader.py 123456")
        print("  python music_downloader.py 123456 lossless")
        print("  python music_downloader.py 123456 hires --download")
        sys.exit(0)

    # 解析参数
    try:
        music_id = int(sys.argv[1])
    except ValueError:
        print(f"错误: music_id 必须是整数，收到: {sys.argv[1]!r}")
        sys.exit(1)

    quality = sys.argv[2] if len(sys.argv) > 2 and not sys.argv[2].startswith("--") else "lossless"
    do_download = "--download" in sys.argv

    # 打印音乐信息
    try:
        info = downloader.get_music_info(music_id, quality)
    except DownloadException as e:
        print(f"获取音乐信息失败: {e}")
        sys.exit(1)

    print()
    print("=" * 70)
    print(f"音乐信息  (ID={info.id}, quality={info.quality})")
    print("=" * 70)

    info_dict = asdict(info)
    for key, value in info_dict.items():
        # 歌词太长，只显示行数
        if key in ("lyric", "tlyric"):
            lines = [ln for ln in (value or "").splitlines() if ln.strip()]
            preview = " | ".join(lines[:2])[:80] if lines else "(空)"
            print(f"  {key:<14}: {len(lines)} 行  预览: {preview}{'...' if len(lines) > 2 else ''}")
            continue
        # URL 类字段：完整展示（如果终端窄可换行）
        if key in ("download_url", "pic_url"):
            print(f"  {key:<14}: {value}")
            continue
        print(f"  {key:<14}: {value}")

    print("=" * 70)

    # 可选：触发下载
    if do_download:
        print()
        print("开始下载...")
        result = downloader.download_music_file(music_id, quality)
        if result.success:
            print(f"下载完成: {result.file_path}  ({result.file_size} 字节)")
        else:
            print(f"下载失败: {result.error_message}")
            sys.exit(1)
