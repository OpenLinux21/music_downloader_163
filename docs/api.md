# 对外 HTTP API 说明

> 本文档描述 Web 服务暴露的全部 HTTP 接口，内容以代码实现与本地实测为准。
> 标注「实测」的样例来自本地 Flask test_client 验证；涉及真实上游调用的成功路径仅依据代码逻辑描述，未做真实外网请求验证。
> 代码变化时须按 `docs/doc-maintenance.md` 同步更新本文档。

## 1. 基础信息

| 项 | 内容 |
| --- | --- |
| Base URL | `http://localhost:5000`（`host`/`port` 读自 `config.toml` 的 `server.host`/`server.port`，默认 `127.0.0.1:5000`） |
| 请求方式 | GET / POST；POST 同时接受 JSON 体与表单（JSON 优先） |
| 响应格式 | JSON（`/download` 默认返回音频文件流，`format=json` 时返回 JSON） |
| 鉴权 | 无。服务不校验调用方身份；上游鉴权依赖 `config.toml` 中服务器默认 Cookie |
| 限速 | slowapi 按来源 IP：业务路由（`/search` `/song` `/playlist` `/album` `/download`）默认 10 次/分钟 + 25 次/小时（读自 `config.toml` 的 `api.*`），超限返回 429 统一结构；`/health`、`/api/info`、`/` 不限速（监控可用性优先） |
| CORS | 跨域请求允许任意来源（`Access-Control-Allow-Origin: *`，CORSMiddleware 实现） |
| Web 框架 | FastAPI（uvicorn 托管）；**2026-11-15 由 Flask 迁移，大写别名路由（`/Song_V1` 等）已移除，属破坏性变更，前端调用已同步改为小写路径** |

## 2. 统一响应结构

成功（`APIResponse.success`，HTTP 200）：

```json
{
  "status": 200,
  "success": true,
  "message": "描述文本",
  "data": { }
}
```

失败（`APIResponse.error`，HTTP 400/404/500）：

```json
{
  "status": 400,
  "success": false,
  "message": "错误描述"
}
```

说明：
- `data` 字段仅成功且确有数据时出现；
- `error_code` 字段在代码中定义但当前所有调用均未传入，实际响应中不会出现；
- 全局错误处理器将 400 统一转为「请求参数错误」、404 转为「请求的资源不存在」、500 转为「服务器内部错误」，均走上述失败结构。

## 3. 音质参数

`/song`（`level`）与 `/download`（`quality`）共用同一组合法值（8 项，代码硬编码校验）：

| 值 | 含义（服务端展示名） |
| --- | --- |
| `standard` | 标准音质 |
| `exhigh` | 极高音质 |
| `lossless` | 无损音质 |
| `hires` | Hi-Res音质 |
| `jyeffect` | 高清环绕声 |
| `sky` | 沉浸环绕声（请求时额外携带 `immerseType=c51`） |
| `jymaster` | 超清母带 |
| `dolby` | 杜比全景声（请求 `encodeType=mp4`） |

> README 中「standard~jyeffect 需黑胶 VIP、sky/jymaster 需 SVIP」的说法来自作者说明，代码层面不做会员校验，实际能否取得取决于上游对 Cookie 的判定。

## 4. 接口列表

### 4.1 `GET /` — Web 首页

返回 `templates/index.html` 渲染结果（HTML 页面，非 JSON）。

### 4.2 `GET /health` — 健康检查

无参数。`data` 结构（实测样例）：

```json
{
  "service": "running",
  "timestamp": 1794746216,
  "cookie_status": "valid",
  "cookie_count": 2,
  "storage_dir": "/app/storage",
  "version": "2.0.0"
}
```

说明：`cookie_status` 仅表示 `config.toml` 的 `server.default_cookie` 是否解析出非空 Cookie（`valid`/`invalid`），**不代表登录态真实有效**；`timestamp` 为 Unix 秒级时间戳。

POST /song`（别名 `/Song_V1`）— 歌曲信息|POST /song`（别名 `/Song_V1`）— 歌曲信息

| 参数 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `ids` / `id` / `url` | 三选一 | 无 | 歌曲 ID 或网易云音乐链接（支持 `music.163.com` 链接与 `163cn.tv` 短链） |
| `level` | 否 | `lossless` | 音质，见第 3 节 |
| `type` | 否 | `url` | 返回内容类型：`url` / `name` / `lyric` / `json` |

各 `type` 的 `data` 结构：

- `type=url`：`{id, url, level, quality_name, size, size_formatted, type, bitrate}`（`type` 为文件格式如 flac/mp3，`bitrate` 为上游返回的码率字段 `br`）。取不到 URL 时返回 404「获取音乐URL失败，可能是版权限制或音质不支持」。
- `type=name`：上游 `api/v3/song/detail` 原始结构（含 `songs` 数组，字段 `ar`/`al`/`dt` 等）。
- `type=lyric`：上游歌词接口原始结构（含 `lrc.lyric` 原文与 `tlyric.lyric` 翻译）。
- `type=json`：聚合结构 `{id, name, ar_name, al_name, pic, level, lyric, tlyric, url, size, level}`；`ar_name` 为逗号分隔歌手串，`size` 为格式化字符串（如 `8.50MB`），URL 获取失败时 `url` 为空串、`size` 为 `获取失败`。

参数校验失败样例（实测）：

```text
GET /song（无参数）
→ 400 {"status":400, "success":false, "message":"必须提供 'ids'、'id' 或 'url' 参数"}

POST /song {"id":"123","level":"xxx"}
→ 400 {"status":400, "success":false, "message":"无效的音质参数，支持: standard, exhigh, lossless, hires, sky, jyeffect, jymaster, dolby"}
```

### 4.4 `GET|POST /search` — 搜索

| 参数 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `keyword` / `keywords` / `q` | 三选一 | 无 | 搜索关键词 |
| `limit` | 否 | `30` | 返回数量，超过 100 截断为 100 |
| `offset` | 否 | `0` | **当前被读取但未生效**（不参与上游请求，见知识库已知问题） |
| `type` | 否 | `1` | **当前被读取但未生效**（上游固定只搜歌曲） |

`data` 为歌曲数组（实测空参数校验：`400 "参数 'keyword' 不能为空"`）：

```json
[
  {
    "id": 185668,
    "name": "稻香",
    "artists": "周杰伦",
    "album": "魔杰座",
    "picUrl": "https://p3.music.126.net/...",
    "artist_string": "周杰伦"
  }
]
```

说明：`artists` 为 `/` 分隔的歌手串；`artist_string` 为路由层追加的重复字段（与 `artists` 同值），为兼容旧前端而保留。

### 4.5 `GET|POST /playlist` — 歌单详情

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | 歌单 ID（路由层不做链接解析，前端负责从链接中提取） |

`data` 结构：`{status: "success", playlist: {id, name, coverImgUrl, creator, trackCount, description, tracks}}`。
`tracks` 为数组，元素 `{id, name, artists, album, picUrl}`；歌曲详情按每批 100 个 ID 向上游补全。

### 4.6 `GET|POST /album` — 专辑详情

| 参数 | 必填 | 说明 |
| --- | --- | --- |
| `id` | 是 | 专辑 ID（同上，链接解析由前端完成） |

`data` 结构：`{status: 200, album: {id, name, coverImgUrl, artist, publishTime, description, songs}}`。
`songs` 元素 `{id, name, artists, album, picUrl}`；封面直链由服务端封面算法生成。

### 4.7 `GET|POST /download` — 下载音乐

| 参数 | 必填 | 默认 | 说明 |
| --- | --- | --- | --- |
| `id` | 是 | 无 | 音乐 ID 或链接（路由层做链接解析与短链展开） |
| `quality` | 否 | `lossless` | 音质，见第 3 节 |
| `format` | 否 | `file` | `file` 返回文件流；`json` 返回下载信息 JSON |
| `user_cookie` | 否 | 空 | **请求级用户凭据**（2026-11-15 新增）：仅保存在用户浏览器 localStorage 并随本次下载请求发送，服务端仅在内存中解析用于本次上游调用，请求结束即丢弃——**不落盘、不写日志、不写任何数据库**（下载文件本身照常纳入缓存与哈希追踪）；缺省时使用 `config.toml` 的服务器默认 Cookie。缓存命中时不消耗凭据（不发起上游调用） |

`format=file`：成功返回音频文件流（`Content-Disposition: attachment`，文件按实际音频格式命名），并附加响应头：

| 响应头 | 内容 |
| --- | --- |
| `X-Download-Message` | `Download completed successfully` |
| `X-Download-Filename` | URL 编码（`quote`，safe 为空）后的实际文件名（如 `歌名.mp3`） |

`format=json`：`data` 结构（2026-11-15 起新增 `cached` 字段）：

```json
{
  "music_id": 185668,
  "name": "歌名",
  "artist": "歌手",
  "album": "专辑",
  "quality": "lossless",
  "quality_name": "无损音质",
  "file_type": "flac",
  "file_size": 8912841,
  "file_size_formatted": "8.50MB",
  "file_path": "/app/storage/185668/歌名.flac",
  "filename": "歌名.flac",
  "duration": 223,
  "cached": false
}
```

说明（2026-11-15 起生效）：
- 缓存键为 `(song_id, quality)`：命中需先通过完整性校验（重算 SHA-256 与 `sha256sum.db` 记录比对），一致才复用文件并累加歌曲访问量，`cached=true`，响应数据读自 `root.db`，不发起上游调用；不一致（或无记录）判定为损坏，服务端删除该音质文件与副本后重新下载、重新写入元数据、重新登记哈希再提供（对调用方表现为一次 `cached=false` 的正常下载）；未命中才真实下载。
- `filename`/`file_path` 均为实际存储文件名（`storage/<song_id>/歌曲名.扩展名`，扩展名冲突时含音质段），原「歌名 [音质]」展示名与双重命名规则已废除（知识库已知问题 #3 已修复）。
- 下载链路只经下载器一次上游交互（链接/详情/歌词一次取齐），原重复调用已消除（知识库已知问题 #5 已修复）；上游取链失败（版权/音质不支持等 `DownloadException`）返回 404。

### 4.8 `GET /api/info` — 服务信息

无参数。`data` 含 `name`（网易云音乐API服务）、`version`（2.0.0）、`endpoints`（7 个端点及说明）、`supported_qualities`（**当前为 7 项，缺 `dolby`，与其他接口校验列表不一致**）、`config`（`storage_dir` 存储目录绝对路径、`max_cache_mb` 缓存上限、`max_file_size` 500MB、`request_timeout` 30s）。

## 5. 输入解析规则（重要）

1. GET 请求参数取自 URL query；POST 请求将表单与 JSON 体合并（JSON 优先覆盖同名字段）。
2. `/song` 与 `/download` 的 ID/链接参数经 `_extract_music_id` 解析：
   - 含 `163cn.tv` → 以 `allow_redirects=False` 请求短链，取 `Location` 响应头作为新链接；
   - 含 `music.163.com` → 定位 `id=` 后截取至下一个 `&`；
   - 其余 → 原样去除首尾空白后当作 ID。
3. `/playlist` 与 `/album` 的 `id` **不做**上述解析，若直接传链接将无法识别（前端负责先行提取）。

## 6. 错误码汇总（业务层主动返回）

| HTTP | 典型 message | 触发场景 |
| --- | --- | --- |
| 429 | `请求过于频繁，请稍后再试` | 业务路由超过每 IP 限速阈值（默认 10/分钟或 25/小时） |
| 400 | `必须提供 'ids'、'id' 或 'url' 参数` | `/song` 缺参数 |
| 400 | `参数 'xxx' 不能为空` | 通用参数校验 |
| 400 | `无效的音质参数，支持: ...` | 音质不在 8 项列表 |
| 400 | `无效的类型参数，支持: url, name, lyric, json` | `/song` 的 `type` 非法 |
| 400 | `返回格式只支持 'file' 或 'json'` | `/download` 的 `format` 非法 |
| 400 | `参数格式错误: ...` | `/search` 的 `limit/offset` 无法转 int |
| 404 | `获取音乐URL失败，可能是版权限制或音质不支持` | 上游未返回 URL |
| 404 | `未找到音乐信息` / `未找到歌曲信息` | 上游详情为空 |
| 404 | `无法获取音乐下载链接，可能是版权限制或音质不支持` / `下载失败: ...` | `/download` 取链或信息获取失败（`DownloadException`） |
| 404 | `文件不存在` | 返回文件时本地文件缺失 |
| 500 | `API调用失败: ...` / `服务器错误: ...` 等 | 上游异常或内部错误 |
