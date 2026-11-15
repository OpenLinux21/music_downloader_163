# 数据流说明

> 本文档描述服务内部与内外的数据流转：请求处理链路、解析聚合流、下载流水线、Cookie 链路与文件存储。
> 内容以当前代码为准，代码变化时须按 `docs/doc-maintenance.md` 同步更新。

## 1. 通用请求处理链路

所有请求（不分接口）都经过以下固定链路：

```text
客户端请求
   │
   ▼
before_request：request.environ 记录 perf_counter() 起始时间
   │
   ▼
Flask 路由分发（每个业务路由均注册小写/大写别名两条规则）
   │
   ▼
_safe_get_request_data()：统一取参
   ├─ GET  → request.args 字典
   └─ POST → {**表单字段, **JSON 字段}（JSON 同名键覆盖表单）
   │
   ▼
路由内参数校验（必填项、音质白名单、类型白名单等，失败即返回 APIResponse.error）
   │
   ▼
_get_cookies() → load_cookies()：每次请求都实时读取 config.toml 的 server.default_cookie 并解析为字典
   │
   ▼
调用 music_api 向上游发起请求（超时 30s，业务码非 200 抛 APIException）
   │
   ▼
整理为前端期望结构 → APIResponse.success / APIResponse.error
   │
   ▼
after_request：追加 4 个 CORS 头，输出单条 GIN 风格请求行
（[SERVER] 时间 | 状态码 | 微秒耗时 | IP | METHOD 路径，见 docs/deployment.md 第 5 节）
```

异常处理分工：`APIException` 与一般异常在各路由内捕获并转为 500 响应；Flask 级 `400/404/500` 错误处理器兜底为统一文案。注意：日志只记录路由路径与状态码等元信息，不记录请求体（为用户自带 Cookie 的不落日志约束打底）。

## 2. ID 与链接解析流（`_extract_music_id`）

```text
输入字符串
   ├─ 含 "163cn.tv"
   │     → GET 短链（不允许跟随重定向，超时 10s）
   │     → 取响应头 Location 作为真实链接继续解析；请求失败则用原串兜底
   ├─ 含 "music.163.com"
   │     → 找到 "id=" 位置 +3，截取到下一个 "&" 之前 → ID
   └─ 其他
         → 去除首尾空白，原样作为 ID
任何异常 → 记录日志，返回原串去除空白（不中断请求）
```

注意：该解析仅用于 `/song` 与 `/download`；`/playlist`、`/album` 的 `id` 参数不做解析，链接由前端先用正则 `playlist\?id=(\d+)` / `album\?id=(\d+)` 提取。

## 3. 单曲聚合解析流（`/song` 且 `type=json`，前端解析页实际使用）

```text
ID/链接 ──► _extract_music_id ──► music_id
   │
   ├─► name_v1(music_id)        → api/v3/song/detail（原始 POST，无 UA 头）
   │     取 songs[0]：name、ar[]（歌手）、al.name、al.picUrl
   ├─► url_v1(music_id, level, cookies) → eapi song/url/v1
   │     取 data[0]：url、size、level（失败则 url=''、size='获取失败'，不报错）
   └─► lyric_v1(music_id, cookies)      → api/song/lyric
         取 lrc.lyric（原文）、tlyric.lyric（翻译）
   │
   ▼
聚合 data = {id, name, ar_name, al_name, pic, level, lyric, tlyric, url, size}
   │
   ▼
前端：解析 LRC → 原文与翻译按时间戳合并 → APlayer 播放 + 歌词滚动
```

## 4. 下载流水线（`/download`，2026-11-15 起缓存键化并消除重复上游调用）

```text
POST /download {id, quality, format}
   │
   ▼
校验 quality（8 项白名单）、format（file/json）
   │
   ▼
_extract_music_id → music_id
   │
   ▼
【缓存查询】storage.get_cached_file(song_id, quality)
   ├─ 命中且哈希校验通过（verify_hash 重算 SHA-256 比对 sha256sum.db）
   │   → 复用文件，响应组装读 root.db（song_with_file），
   │     路由层 record_access(song_id) 累加访问量 → cached=true
   │     （不发起任何上游调用）
   ├─ 命中但校验不通过（不一致/无记录）→ invalidate_file 删除该音质主文件与副本
   │   及 files 行、哈希记录 → 走下方未命中路径重新下载并重新登记哈希
   └─ 未命中 ↓
   │
   ▼
【唯一一次上游交互】downloader.get_music_info(music_id, quality)
   ├─ url_v1：播放/下载 URL（无 URL 抛 DownloadException → 路由返回 404）
   ├─ song/detail v3：歌名/歌手/专辑(含 album_id)/封面
   └─ lyric：原文 + 翻译
   │
   ▼
【落盘】路径 = storage/<song_id>/歌曲名[.音质].扩展名（扩展名判定链不变）
   下载经传输层：单流或 4 段 Range 并发（multi_thread 开关，>512KB 才分段，
   失败自动回退单流）+ 共享令牌节流（speed_limit_kb，0 不限速）
   → *.part 临时文件 → 原子改名
   │
   ▼
【后处理 _post_register】（顺序保证 DB 大小与落盘一致）
   ├─ 封面下载一次 → storage/<song_id>/cover.jpg（同时供 ffmpeg 嵌入）+ 哈希登记
   ├─ 歌词 → storage/<song_id>/lyric.lrc + 哈希登记
   ├─ ffmpeg 写元数据+封面（失败不阻断）
   ├─ register_song / register_file（root.db 登记）+ register_hash（sha256sum.db）
   └─ 归属专辑 → copy2 副本到 storage/albums/<album_id>/<song_id>/ 并登记（is_copy=1）+ 哈希登记
   │
   ▼
file → send_file 文件流 + X-Download-Message / X-Download-Filename（实际文件名，URL 编码）
json → 下载信息 JSON（含 cached 字段）
```

原「路由层先调 name_v1+url_v1、下载器内部再调一轮」的重复上游调用已消除（知识库已知问题 #5）；原「歌名 [音质]」展示名缓存检查失效问题随缓存键化一并修复（知识库已知问题 #3）。

## 5. ffmpeg 元数据写入流（`MusicDownloader._write_metadata_with_ffmpeg`）

```text
入口条件检查：ffmpeg 可用（懒加载 which）∧ 扩展名 ∈ {.mp3,.flac,.m4a,.mp4,.ogg,.opus} ∧ 文件存在
   │
   ▼
封面来源：_post_register 已落盘的 cover.jpg（cover_local 参数，不再重复下载）；
          未提供时回退下载到 storage/ 下临时 .jpg（失败则跳过封面，不阻断）
   │
   ▼
构造命令：ffmpeg -y -loglevel error
          -i 音频文件 [-i 封面]
          -metadata title/artist/album/album_artist/comment/lyrics[/track/tracktotal]
          + 各格式专属参数（见下表）
          [-f 输出 muxer] 输出到 "原名.tagging.原扩展名"
   │
   ▼
subprocess.run 超时 120s
   ├─ 成功 → shutil.move 原子替换原文件 → True
   └─ 失败/超时/异常 → DOWNLOADER 日志告警 → False（下载结果不受影响）
   │
   ▼
finally：删除封面临时文件（已落盘 cover.jpg 不删）与残留 .tagging 文件
```

歌词嵌入（`lyrics` 键，2026-11-15 新增）：歌词原文非空时随 `-metadata lyrics=` 写入，
使文件独立携带歌词、无需外部 .lrc。本机 ffmpeg 9.0.2 逐格式 ffprobe 实测：
`.mp3` 写入 ID3v2 USLT、`.flac` 写入 Vorbis `LYRICS` 注释、`.m4a` 写入 `©lyr` 原子，
三种格式确认支持；`.ogg`/`.opus`/`.mp4` 未逐一实测（失败不阻断）。

各格式专属参数（`_build_format_args`）：

| 扩展名 | muxer | 有封面时 | 特殊点 |
| --- | --- | --- | --- |
| `.mp3` | mp3 | `-map 0:a -map 1:v`，ID3v2.3，封面流标记 `Cover (front)` | 封面经 ID3v2 APIC 写入 |
| `.flac` | flac | `-map 0 -map 1 -c copy`，封面流 `attached_pic` | FLAC 原生封面 |
| `.m4a` | mp4 | `-map 0:a -map 1:v`，`attached_pic` | AAC + mp4 容器 |
| `.mp4` | mp4 | `-map 0 -map 1:v`，`attached_pic` + `-movflags +faststart` | 杜比全景声（EAC3）分支，moov 前置 |
| `.ogg`/`.opus` | ogg/opus | `-map 0:a -map 1:v`，音视频流均 copy | — |

## 6. Cookie 数据链路

```text
【写入侧】
  人工编辑 config.toml 的 server.default_cookie（"k1=v1; k2=v2" 或每行一对）
  或 python qr_login.py login → save_cookie()：备份旧值（cookie.backup.backup.时间戳.txt）
      → 经 config_loader.save_default_cookie 写回 config.toml → 校验含 MUSIC_U/__csrf/NMTID 任一关键字段

【读取侧（每请求执行，无缓存）】
  load_cookies()
    → config_loader.get_default_cookie() 实时读取 config.toml（文件缺失/未配置返回空串，不抛异常）
    → parse_cookie_string：支持 ";" 或 "\n" 分隔，"k=v" 解析为字典
    → config.toml 存在但 TOML 解析失败时，get_default_cookie 会抛 TOMLDecodeError
      （start.py 启动自检会先行拦截并逐行报告；若绕过 start.py 直接导入 main.py，该异常在导入阶段抛出，服务拒绝以错误配置启动）
  HTTPClient 发 eapi 请求时：DEFAULT_COOKIES{os=pc, deviceId=pyncm!...} 先入，
  用户 Cookie 后合并覆盖（用户值优先）
```

## 7. 文件存储流

| 路径 | 写入者 | 时机 | 清理策略 |
| --- | --- | --- | --- |
| `config.toml` | 人工 / `qr_login.py` / 服务启动读取 | 部署时从模板复制并填写 | Git 不跟踪（.gitignore、.dockerignore 均已排除） |
| `cookie.backup.*.txt` | `qr_login.py` | 覆盖/清除 Cookie 前 | 无自动清理，Git 不跟踪 |
| `storage/root.db` | `storage_manager.py` | 服务启动建库；下载/命中/清理时读写 | Git 不跟踪 |
| `storage/sha256sum.db` | `storage_manager.py` | 文件入缓存时登记哈希；命中前重算校验 | Git 不跟踪 |
| `storage/<song_id>/…` | `MusicDownloader`（经 `_post_register`） | 首次下载落盘并登记；命中复用不写盘 | 3 个月到期删除 / 超限按访问量淘汰（见 `docs/database.md`） |
| `storage/albums/<album_id>/<song_id>/…` | `MusicDownloader` | 下载完成后 copy2 副本 | 随歌曲一并清理 |
| `storage/<song_id>/*.part` | 下载临时文件 | 下载期间 | 完成后原子改名 |
| `logs/service.log`（+ 轮转备份） | `logger.py` | 每次请求与组件事件 | 5MB 轮转保留 3 份，Git 不跟踪 |
| `downloads/歌手 - 歌名.扩展名` | `MusicDownloader` | 每次下载 | 无自动清理，同名跳过（按下载器命名） |
| `downloads/*.tagging.*` | ffmpeg 临时输出 | 元数据写入期间 | 成功后 move、失败后 finally 删除 |
| `downloads/tmp*/***.jpg`（mkstemp） | 封面下载 | 元数据写入期间 | finally 删除 |
