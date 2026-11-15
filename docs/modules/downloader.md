# 模块详解：音乐下载器（music_downloader.py）

> 本文档详解下载与元数据写入模块。修改该文件前须先读取 `docs/skills/!dev.md` 与本文档。
> 下载链路整体数据流见 `docs/data-flow.md` 第 4、5 节。代码变化时须按 `docs/doc-maintenance.md` 同步更新。

## 1. 模块职责

- 获取单曲完整信息（URL、详情、歌词）并落盘下载（同步/异步/批量/内存四种方式）；
- 基于外部 ffmpeg 为音频文件写入标签元数据与专辑封面；
- 提供命令行入口，用于单独查询或下载单曲。

Web 服务仅使用其中的 `download_music_file` 同步路径；异步/内存/批量能力当前无调用方，属模块预留能力。

## 2. 类型定义

| 名称 | 内容 |
| --- | --- |
| `AudioFormat`(Enum) | mp3/flac/m4a/mp4/unknown（当前未被业务逻辑引用） |
| `QualityLevel`(Enum) | 与 `music_api.py` 重复定义（8 项，未被引用，已知问题 #9） |
| `MusicInfo`(dataclass) | 14 字段：id、name、artists、album、**album_id**（2026-11-15 新增，取自上游 `al.id`）、pic_url、duration（秒，由上游毫秒 `dt//1000`）、track_number、download_url、file_type、file_size、quality、lyric、tlyric |
| `DownloadResult`(dataclass) | success、file_path、file_size、error_message、music_info、**cached**（2026-11-15 新增，是否命中缓存复用） |
| `DownloadException` | 下载域异常（API 异常在此包装） |

映射表常量：

| 常量 | 内容 |
| --- | --- |
| `API_TYPE_TO_EXT` | 上游 `type`（mp3/flac/m4a/mp4/ogg/opus）→ 扩展名 |
| `EXT_TO_FFMPEG_FORMAT` | 扩展名 → ffmpeg 输出 muxer（`.m4a` 与 `.mp4` 均为 `mp4`） |
| `METADATA_FORMATS` | 支持写元数据的扩展名集合（mp3/flac/m4a/mp4/ogg/opus） |

## 3. MusicDownloader 构造与工具方法

```python
MusicDownloader(storage_dir="storage", max_concurrent=3, ffmpeg_path=None,
                storage_manager=None, multi_thread=False, speed_limit_kb=0)
```

- 存储构造：`storage_manager` 注入外部 `StorageManager`（服务侧传入带缓存上限配置的实例）；
  缺省时按 `storage_dir` 自建（不限总量、默认 90 天保留期）。存储布局与 DB 设计见 `docs/database.md`。
- 传输配置：`multi_thread`（单文件多线程分段下载开关）与 `speed_limit_kb`（KB/s，0 不限速）
  读自 `config.toml` 的 `[download]`，由服务侧构造时传入。
- 构造时内部新建一份 `NeteaseAPI`、用 `shutil.which("ffmpeg")` 做首次检测（缺失仅经 `DOWNLOADER` 日志器告警，不报错）。
- 日志：模块内事件统一经 `logger.get_logger("DOWNLOADER")` 输出（GIN 风格），CLI 入口（`__main__`）的交互输出仍为 print。
- `_resolve_ffmpeg()`：每次写元数据前懒加载重试并回写缓存，支持服务运行中途安装 ffmpeg。
- `_sanitize_filename()`：替换 `<>:"/\|?*` 为 `_`、去首尾空格与点、超 200 字符截断、空串回退 `unknown`。
- `_determine_file_extension()` 优先级：API `type` > URL 后缀（.flac/.mp3/.m4a|.mp4）> Content-Type（flac/mpeg|mp3/mp4|m4a）> 默认 `.mp3`。
- `_build_file_path()`：`storage/<song_id>/歌曲名[.音质].扩展名`（音质段仅在扩展名被其他音质占用时追加）。

## 3.1 下载后登记 `_post_register(music_info, file_path)`

下载完成后统一执行（同步/异步共用）：

1. `register_song`：登记歌曲元数据（UPSERT，保留累计访问量）；
2. 封面下载一次 → `storage/<song_id>/cover.jpg`（同时作为 ffmpeg 嵌入源，`_write_metadata_with_ffmpeg` 的 `cover_local` 参数）+ `register_hash`；
3. 歌词 → `storage/<song_id>/lyric.lrc`（空歌词跳过）+ `register_hash`；
4. ffmpeg 写元数据与封面（失败不阻断）；
5. `register_file`：登记音质文件（**在元数据写入之后取大小**，保证 DB 与落盘一致）+ `register_hash`；
6. 归属专辑（`album_id` 非空）→ `copy2` 副本至 `storage/albums/<album_id>/<song_id>/`（含封面歌词）并以 `is_copy=1` 登记 + 各副本 `register_hash`。

缓存命中（`get_cached_file`）时先执行 `verify_hash`（重算 SHA-256 比对 `sha256sum.db`）：一致则复用并由 root.db 记录构建 `MusicInfo`（`_music_info_from_record`），不发起上游调用；不一致或无记录判定损坏，`invalidate_file` 删除该音质主文件与副本后进入重新下载分支（重下后重新完成元数据写入并重新登记哈希）。

## 3.5 传输层（2026-11-15 新增）

| 组件 | 行为 |
| --- | --- |
| `_RateLimiter(rate_kb)` | 按「已发字节 / 目标速率」均匀节流，多线程共享实例；`rate_kb=0` 零等待 |
| `_download_single_stream(url, dest, limiter)` | 单流 64KB 分块下载，逐块限速 |
| `_download_ranged(url, start, end, dest, limiter)` | Range 分段下载，非 206 响应抛错 |
| `_download_to(url, dest, total_size, limiter)` | 总入口：`multi_thread=True` 且总大小 > 512KB 时按 **4 段 Range 并发**（`ThreadPoolExecutor`）下载到 `.part.p{i}` 后顺序拼接并校验总大小；总大小未知、任一分段失败或大小不匹配时清理分段并**自动回退单流** |

仅同步路径（服务实际使用的 `download_music_file`）接入传输层；异步/批量预留路径未接入（见第 5 节）。

## 4. 信息获取 `get_music_info(music_id, quality)`

执行顺序：`cookies`（请求级凭据，`None` 时回落 `load_cookies()` 读 config）→ `api.get_song_url()` → `api.get_song_detail()` → `api.get_lyric()` → 组装 `MusicInfo`。任一环节失败抛 `DownloadException`。（原调试残留 `print(url_result)` 已于 2026-11-15 移除，知识库已知问题 #6 已修复）

## 5. 四种下载方式

| 方法 | I/O 模型 | 落盘 | 写元数据 | 当前调用方 |
| --- | --- | --- | --- | --- |
| `download_music_file` | requests 流式（8192 分块） | 是 | 是 | `/download` 路由、CLI |
| `download_music_file_async` | aiohttp + aiofiles（8192 分块） | 是 | 是 | 无（预留） |
| `download_music_to_memory` | requests 一次性读入 | 否（BytesIO） | 否 | 无（预留） |
| `download_batch_async` | asyncio + `Semaphore(max_concurrent)` 并发包装异步下载 | 是 | 是 | 无（预留） |

共性逻辑（2026-11-15 缓存键化 + 哈希校验后）：先查 `storage.get_cached_file(song_id, quality)` 并 `verify_hash` 校验完整性，一致才复用（`cached=true`，不重写元数据、不发起上游调用）；不一致或无记录判定损坏，`invalidate_file` 删除后走重新下载（重下后重新登记哈希）；未命中才下载（先写 `.part` 临时文件，完成后原子改名）。网络失败返回 `success=False` 的 `DownloadResult`（不抛异常）；信息获取失败抛 `DownloadException`。

`download_batch_async` 对每个任务的异常降级为失败结果列表项，保证 `gather` 不中断。

## 6. 进度查询 `get_download_progress(music_id, quality)`

按期望文件名检查磁盘：文件存在则 `progress = 当前大小 / 期望大小 × 100`（`min` 截断 100），`completed` 以 `当前大小 ≥ 期望大小` 判定；文件不存在返回 0。异常时返回带 `error` 的结构。注意：期望大小来自 API 元数据，ffmpeg 重写文件后大小可能变化，导致进度计算偏差（知识库待确认 #5）。

## 7. ffmpeg 元数据写入（核心后处理）

完整流程图与各格式参数表见 `docs/data-flow.md` 第 5 节，此处补充实现约束：

1. **前置检查**：ffmpeg 可解析、扩展名 ∈ `METADATA_FORMATS`、文件存在；任一不满足直接返回 `False` 并经 `DOWNLOADER` 日志器告警。
2. **封面**：优先使用 `_post_register` 已落盘的 `storage/<song_id>/cover.jpg`（`cover_local` 参数）；未提供时回退下载为 `storage/` 下 `mkstemp` 临时 `.jpg`（finally 清理，已落盘文件不删）；下载失败降级为无封面继续。
3. **命令构造顺序**：全局选项 → `-i` 音频 →（可选）`-i` 封面 → `-metadata` 序列 → 格式专属参数 → `-f` muxer（必须位于 `-i` 之后作为输出选项）→ 输出文件 `原名.tagging.原扩展名`。
4. **写入的元数据键**：`title`、`artist`、`album`、`album_artist`、`comment`（固定为 `Downloaded from Netease Cloud Music`）、`track`/`tracktotal`（`track_number > 0` 时，两者同值）、**`lyrics`**（歌词原文非空时写入，2026-11-15 新增）。歌词嵌入经本机 ffmpeg 9.0.2 逐格式实测（ffprobe 验证）：`.mp3`→ID3v2 USLT、`.flac`→Vorbis `LYRICS` 注释、`.m4a`→`©lyr` 原子，三种格式均确认可写回；`.ogg`/`.opus`/`.mp4` 未逐一实测，行为随 muxer 而定（失败不阻断，见第 6 条）。
5. **原子性**：`subprocess.run` 成功且临时文件存在时 `shutil.move` 替换原文件；超时 120 秒；`finally` 中清理封面临时文件与残留 `.tagging` 文件。
6. **失败语义**：一切失败仅经 `DOWNLOADER` 日志器告警（WARNING/ERROR）并返回 `False`，调用方（下载流程）不受影响。

## 8. 命令行入口（CLI）

```bash
python music_downloader.py                       # 打印模块能力与 ffmpeg 状态
python music_downloader.py <music_id>            # 查询信息（quality 缺省 lossless）
python music_downloader.py <music_id> hires      # 指定音质查询
python music_downloader.py <music_id> hires --download  # 查询并下载
```

输出：`MusicInfo` 全字段（歌词仅显示行数与前两行预览）；`--download` 时追加下载结果，失败以退出码 1 结束。

## 9. 修改注意事项

1. 文件命名或扩展名判定变化必须同步 `docs/data-flow.md`，并复核 `main.py` `/download` 的缓存检查路径是否仍与其不一致（若修复一致性问题，需同时更新知识库已知问题 #3 与 `docs/api.md`）。
2. ffmpeg 分支参数变化须在 `docs/data-flow.md` 第 5 节表格同步；新增容器格式时同步 `API_TYPE_TO_EXT`/`EXT_TO_FFMPEG_FORMAT`/`METADATA_FORMATS` 三张表。
3. `print` 输出（含已知问题 #6 的调试残留）会进入容器 stdout 日志，新增输出建议改用 logging（需保持现有行为时在任务中说明）。
4. 音质列表调整时注意本模块 `QualityLevel` 枚举与 CLI 帮助文本中的音质串。
