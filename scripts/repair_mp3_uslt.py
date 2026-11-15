"""一次性存量修复：mp3 歌词补写为标准 ID3v2 USLT 帧

背景（详见 docs/knowledge-base.md）：ffmpeg 的 mp3 muxer 写不出 USLT 帧，
`-metadata lyrics=` 退化为 TXXX:USLT 自定义帧，主流播放器不识别为歌词。
2026-11-16 起下载链路已在 ffmpeg 写入后由 mutagen 补写标准 USLT 帧
（`MusicDownloader._embed_mp3_uslt`），本脚本将存量 mp3 一并修复。

处理决策（按文件逐一判定，幂等可重复执行）：
- 已存在标准 USLT 且无 TXXX:USLT → 视为已修复，跳过
- 存在 TXXX:USLT → 取帧内歌词文本补写标准 USLT 并清除退化帧
- 两者皆无且 storage/<song_id>/lyric.lrc 存在 → 从 lrc 注入
  （覆盖歌词功能上线前下载、完全无歌词的存量文件）
- 其余（无歌词来源、损坏/伪造 mp3）→ 跳过并记录

修复后经 StorageManager.register_file / register_hash 同步 root.db 与
sha256sum.db：文件字节已变化，不重登记会导致下次访问哈希校验失败而被
误判损坏、触发不必要的重新下载。

运行：python scripts/repair_mp3_uslt.py [storage_dir]（默认 storage）
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mutagen.id3 import ID3

from music_downloader import MusicDownloader
from storage_manager import StorageManager


def main() -> int:
    storage_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("storage")
    storage = StorageManager(storage_dir)
    downloader = MusicDownloader(storage_manager=storage)

    # 只读查询 files 表（维护脚本不新增 StorageManager 公开方法）
    con = sqlite3.connect(storage.db_path)
    rows = con.execute(
        "SELECT song_id, quality, path, size, is_copy FROM files WHERE ext = '.mp3'"
    ).fetchall()
    con.close()

    repaired, skipped = [], []
    for song_id, quality, rel_path, _old_size, is_copy in rows:
        path = storage_dir / rel_path
        if not path.is_file():
            skipped.append((rel_path, "文件不存在"))
            continue

        try:
            tags = ID3(path)
        except Exception as e:
            skipped.append((rel_path, f"mp3 解析失败: {e.__class__.__name__}"))
            continue
        has_uslt = bool(tags.getall("USLT"))
        degenerated = tags.getall("TXXX:USLT")

        if has_uslt and not degenerated:
            skipped.append((rel_path, "已是标准 USLT"))
            continue
        if degenerated:
            lyric = str(degenerated[0].text[0])
            source = "TXXX:USLT 退化帧"
        else:
            lrc_path = storage.song_dir(song_id) / "lyric.lrc"
            if not lrc_path.is_file():
                skipped.append((rel_path, "无歌词来源"))
                continue
            lyric = lrc_path.read_text(encoding="utf-8")
            source = "lyric.lrc"

        if not downloader._embed_mp3_uslt(path, lyric):
            skipped.append((rel_path, "USLT 写入失败"))
            continue

        # 字节已变化：同步登记表，保持哈希校验与缓存复用可用
        storage.register_file(song_id, quality, path, path.stat().st_size,
                              is_copy=bool(is_copy))
        storage.register_hash(path, song_id=song_id,
                              quality=None if is_copy else quality)
        repaired.append((rel_path, source))

    print(f"扫描 mp3 登记 {len(rows)} 条")
    for rel, src in repaired:
        print(f"  修复 {rel}（来源: {src}）")
    for rel, reason in skipped:
        print(f"  跳过 {rel}（{reason}）")
    print(f"完成: 修复 {len(repaired)}，跳过 {len(skipped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
