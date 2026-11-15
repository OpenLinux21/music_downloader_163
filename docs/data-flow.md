# 数据流说明

> 本文档描述服务内部与内外的数据流转：请求处理链路、解析聚合流、下载流水线、Cookie 链路与文件存储。
> 内容以当前代码为准，代码变化时须按 `docs/doc-maintenance.md` 同步更新。

## 1. 通用请求处理链路

所有请求（不分接口）都经过以下固定链路：

```text
客户端请求
   │
   ▼
before_request：记录「方法 路径 - IP - User-Agent」到日志
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
_get_cookies() → load_cookies()：每次请求都重新读取 cookie.txt 为字典
   │
   ▼
调用 music_api 向上游发起请求（超时 30s，业务码非 200 抛 APIException）
   │
   ▼
整理为前端期望结构 → APIResponse.success / APIResponse.error
   │
   ▼
after_request：追加 4 个 CORS 头，记录响应状态码日志
```

异常处理分工：`APIException` 与一般异常在各路由内捕获并转为 500 响应；Flask 级 `400/404/500` 错误处理器兜底为统一文案。

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

## 4. 下载流水线（`/download`，当前实现存在重复上游调用）

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
【第 1 次上游调用】name_v1(music_id)      → 歌名/歌手/专辑/封面（校验歌曲存在）
【第 2 次上游调用】url_v1(music_id, quality, cookies) → 下载 URL（失败即 404）
   │
   ▼
构造展示文件名 filename = "歌名 [quality].扩展名"，清洗非法字符
   file_path = downloads/filename
   │
   ▼
if file_path 存在 → 视为缓存命中，跳过下载
   （注：下载器实际保存名为 "歌手 - 歌名.扩展名"，与本检查路径不一致，
     该缓存检查基本不生效 —— 见知识库已知问题 #3）
   │
   ▼
【第 3~5 次上游调用】downloader.download_music_file(music_id, quality)
   ├─ get_music_info：再次 url_v1（+print 调试残留）、song/detail、lyric
   ├─ requests 流式下载 URL → downloads/"歌手 - 歌名.扩展名"
   └─ _write_metadata_with_ffmpeg：写元数据 + 封面（失败不阻断）
   │
   ▼
file_path ← 下载器返回的真实路径
   │
   ├─ format=json → 返回下载信息 JSON（含 file_path 真实绝对路径）
   └─ format=file → send_file 文件流
        + X-Download-Message / X-Download-Filename（展示名，URL 编码）响应头
        前端读响应头命名 blob 触发浏览器保存
```

## 5. ffmpeg 元数据写入流（`MusicDownloader._write_metadata_with_ffmpeg`）

```text
入口条件检查：ffmpeg 可用（懒加载 which）∧ 扩展名 ∈ {.mp3,.flac,.m4a,.mp4,.ogg,.opus} ∧ 文件存在
   │
   ▼
下载封面 pic_url → download_dir 下临时 .jpg（失败则跳过封面，不阻断）
   │
   ▼
构造命令：ffmpeg -y -loglevel error
          -i 音频文件 [-i 封面]
          -metadata title/artist/album/album_artist/comment[/track/tracktotal]
          + 各格式专属参数（见下表）
          [-f 输出 muxer] 输出到 "原名.tagging.原扩展名"
   │
   ▼
subprocess.run 超时 120s
   ├─ 成功 → shutil.move 原子替换原文件 → True
   └─ 失败/超时/异常 → 打印警告 → False（下载结果不受影响）
   │
   ▼
finally：删除封面临时文件与残留 .tagging 文件
```

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
  人工编辑 cookie.txt（"k1=v1; k2=v2" 或每行一对）
  或 python music_api.py → qr_login()：终端二维码 → 轮询 801/802/803
      → 803 时从 Set-Cookie 提取 MUSIC_U → 返回 "MUSIC_U=xxx;os=pc;appver=8.9.70;"
  或 python qr_login.py login → save_cookie()：备份旧文件（cookie.txt.backup.时间戳）
      → 覆盖写入 → 校验含 MUSIC_U/__csrf/NMTID 任一关键字段

【读取侧（每请求执行，无缓存）】
  load_cookies("cookie.txt")
    → 文件不存在/读取失败 → {}（不抛异常）
    → parse_cookie_string：支持 ";" 或 "\n" 分隔，"k=v" 解析为字典
  HTTPClient 发 eapi 请求时：DEFAULT_COOKIES{os=pc, deviceId=pyncm!...} 先入，
  用户 Cookie 后合并覆盖（用户值优先）
```

## 7. 文件存储流

| 路径 | 写入者 | 时机 | 清理策略 |
| --- | --- | --- | --- |
| `cookie.txt` | 人工 / `qr_login.py` | 登录或手动更新 | `logout` 命令删除（先备份 `.logout.时间戳`） |
| `cookie.txt.backup.*` / `.logout.*` | `qr_login.py` | 覆盖/清除 Cookie 前 | 无自动清理 |
| `downloads/歌手 - 歌名.扩展名` | `MusicDownloader` | 每次下载 | 无自动清理，同名跳过（按下载器命名） |
| `downloads/*.tagging.*` | ffmpeg 临时输出 | 元数据写入期间 | 成功后 move、失败后 finally 删除 |
| `downloads/tmp*/***.jpg`（mkstemp） | 封面下载 | 元数据写入期间 | finally 删除 |
| `music_api.log` | `main.py` 日志 | 每次请求与启动 | 无轮转，持续追加 |
