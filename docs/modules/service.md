# 模块详解：Web 服务层（main.py，FastAPI）

> 本文档详解 `main.py` 的内部实现。修改该文件前须先读取 `docs/skills/!dev.md` 与本文档；涉及对外接口变化时还须同步 `docs/api.md` 与 `docs/modules/frontend.md`（映射规则见 `docs/doc-maintenance.md`）。
> 接口的对外行为（参数/响应/错误码/限速）以 `docs/api.md` 为准，本文聚焦内部实现。代码变化时须按 `docs/doc-maintenance.md` 同步更新。

## 1. 模块职责

- 定义 FastAPI 应用与全部 HTTP 路由（唯一路由层；服务启动入口为 `start.py`，运行时由 uvicorn 托管）；
- 参数校验、ID/链接解析、上游调用编排、响应格式统一；
- 初始化 `SERVER` 统一日志器（GIN 风格，见 `logger.py`）、CORS 中间件与 slowapi 限速器；
- 2026-11-15 由 Flask 迁移为 FastAPI：大写别名路由（`/Song_V1` 等）**已移除**（破坏性变更，前端调用已同步改为小写路径），`main.py` 不再含任何启动逻辑。

## 2. 配置类 `APIConfig`（dataclass）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `host` | `127.0.0.1`（实例化时读自 `config.toml` 的 `server.host`） | 监听地址 |
| `port` | `5000`（实例化时读自 `config.toml` 的 `server.port`） | 监听端口 |
| `debug` | `False` | 预留（当前 uvicorn 启动未使用） |
| `storage_dir` | `storage` | 存储根目录（相对项目根，按歌曲 ID 分类） |
| `max_cache_mb` | `8192`（读自 `cache.max_size_mb`） | 本地缓存总大小上限（MB），超限按访问量淘汰 |
| `multi_thread` | `False`（读自 `download.multi_thread`） | 单文件多线程（Range 分段）下载开关 |
| `speed_limit_kb` | `0`（读自 `download.speed_limit_kb`） | 下载限速（KB/s），0 不限速 |
| `rate_per_minute` | `10`（读自 `api.rate_limit_per_minute`） | API 限速：每 IP 每分钟次数 |
| `rate_per_hour` | `25`（读自 `api.rate_limit_per_hour`） | API 限速：每 IP 每小时次数 |
| `max_file_size` | 500MB | 仅在 `/api/info` 中展示，**无实际限制逻辑** |
| `request_timeout` | 30 | 仅在 `/api/info` 中展示，**未传递给上游调用**（上游超时硬编码在 `music_api`） |
| `log_level` | `INFO` | 统一日志级别（`SERVER` 组件，经 `logger.get_logger` 生效） |
| `cors_origins` | `*` | CORS 允许来源 |

注意：`max_file_size` 与 `request_timeout` 是展示性配置，调整它们不会改变行为。`host`/`port` 等的 dataclass 默认值仅在 `config.toml` 缺失对应键时生效；正常运行取值来自 `config.toml`。

## 3. 响应工具 `APIResponse`

- `success(data, message, status_code=200)`：返回 `JSONResponse`，结构 `{status, success, message, data?}`；
- `error(message, status_code=400, error_code=None)`：结构 `{status, success, message, error_code?}`，当前所有调用均不传 `error_code`。

所有路由都返回该结构，是前后端契约的基座，改动属接口破坏性变更。

## 4. 服务类 `MusicAPIService`

实例化时机：**模块导入时**（`api_service = MusicAPIService(config)`），即 `import main` 就会产生副作用：初始化 `NeteaseAPI`、创建 `StorageManager`（建 `storage/` 与 `root.db`/`sha256sum.db` 并立即执行一轮清理）、启动清理守护线程、初始化 `MusicDownloader`（注入同一存储管理器与传输配置）、初始化 `SERVER` 统一日志器。

关键内部方法：

| 方法 | 行为要点 |
| --- | --- |
| 日志 | `get_logger("SERVER", config.log_level)`：GIN 风格统一日志（`logger.py`），控制台 ANSI 着色 + `logs/service.log` 5MB 轮转 ×3（无着色） |
| 会话管理 | `session_manager = SessionManager(config.storage_dir)`（模块级别名暴露）：防刷 Cookie 签发/校验与排行榜（详见 `docs/database.md` 第 4 节） |
| `_get_cookies` | 调 `load_cookies()` 每请求实时读 `config.toml` 的 `server.default_cookie`；未配置打 warning 返回 `{}`；异常返回 `{}` 不中断 |
| `_extract_music_id` | 短链 `163cn.tv` 展开（不跟随重定向取 Location）→ `music.163.com` 提取 `id=` → 否则原样 strip；任何异常返回原串 |
| `_format_file_size` | B/KB/MB/GB/TB 自动换算，两位小数 |
| `_get_quality_display_name` | 8 种音质的中文名映射，未知名返回 `未知音质(xxx)` |
| `_validate_request_params` | 任一参数为假值即返回 `(错误文案, 400)` 元组 |

## 5. 应用装配（中间件 / 依赖 / 异常处理）

| 组件 | 行为 |
| --- | --- |
| `session_middleware` 中间件 | 每请求解析防刷 Cookie（先验签再检查时效）：有效复用会话 ID，无效/过期重新签发并经 Set-Cookie 下发（`nu_session`，HttpOnly + SameSite=Lax + 7 天）；会话 ID 写入 `request.state.session_id` |
| `log_request_line` 中间件 | 每请求输出一条 GIN 风格请求行（`[SERVER] 时间 \| 状态码 \| 微秒耗时 \| IP \| METHOD 路径`）；不记录请求体 |
| `CORSMiddleware` | `allow_origins=["*"]`、methods `GET,POST,OPTIONS`、headers `Content-Type,Authorization`、Max-Age 3600（跨域请求时由中间件追加允许头） |
| `get_request_data` 依赖 | 统一取参：GET 取 query；POST 合并表单与 JSON（JSON 同名键覆盖表单）；`async def` 依赖先行执行，路由本体为同步 `def`（FastAPI 自动入线程池，与同步上游调用/sqlite 短连接兼容） |
| `limiter`（slowapi） | `key_func=get_remote_address`（来源 IP），阈值 `RATE_MINUTE`/`RATE_HOUR` 读自配置；以装饰器应用于 `/search` `/song` `/playlist` `/album` `/download` 五个业务路由；`/health`、`/api/info`、`/` 不限速（监控可用性优先，见 `docs/api.md`） |
| 异常处理器 | `RateLimitExceeded` → 429 统一结构（「请求过于频繁」）；`StarletteHTTPException` 404 → 「请求的资源不存在」；未处理异常 → 500 「服务器内部错误」（记录 `SERVER` 日志含堆栈） |

## 6. 路由清单与实现要点

| 路由 | 视图函数 | 限速 | 实现要点 |
| --- | --- | --- | --- |
| `GET /` | `index` | 否 | `FileResponse` 托管 `templates/index.html`（过渡期旧页面，第十一步替换） |
| `GET /health` | `health_check` | 否 | 重读 Cookie 判 `valid/invalid`（仅判非空）；返回 `storage_dir`、版本 2.0.0 |
| `GET\|POST /song` | `get_song_info` | 是 | 见下文 6.1 |
| `GET\|POST /search` | `search_music_api` | 是 | `keyword/keywords/q` 三参兼容；`limit` 上限 100；结果每项追加 `artist_string` |
| `GET\|POST /playlist` | `get_playlist` | 是 | 包装为 `data={status:'success', playlist:结果}`；**id 不做链接解析** |
| `GET\|POST /album` | `get_album` | 是 | 包装为 `data={status:200, album:结果}`；**id 不做链接解析** |
| `GET\|POST /download` | `download_music_api` | 是 | 见下文 6.2 |
| `GET /rank` | `download_rank` | 是 | 下载排行榜：窗口内独立会话数 TOP 20，并列按名称；实现见 `docs/database.md` 第 4 节 |
| `GET /recommend` | `recommend_api` | 是 | 智能推荐（见下文 6.3） |
| `GET /api/info` | `api_info` | 否 | 服务元信息；`supported_qualities` 硬编码 7 项缺 `dolby`（已知问题 #4） |

### 6.1 `/song` 实现

1. 校验 `level` ∈ 8 项白名单（含 `dolby`）、`type` ∈ `url/name/lyric/json`；
2. `type=url`：`url_v1` 成功后取 `data[0]` 组装响应（含 `size_formatted`、`quality_name`）；空数据返回 404；
3. `type=name`/`lyric`：直接透传上游结构；
4. `type=json`：顺序调用 `name_v1` + `url_v1` + `lyric_v1` 三次上游，聚合为前端解析页所需结构；URL 失败不报错（`url=''`、`size='获取失败'`）。

### 6.2 `/download` 实现（2026-11-15 重写）

1. 校验 `quality`、`format`；
2. 读取可选 `user_cookie`：内存中 `parse_cookie_string` 解析为请求级凭据（无则 `None`，上游回落 config 默认 Cookie）；凭据不落盘、不写日志、不写 DB，请求结束随局部变量丢弃；
3. 委托 `downloader.download_music_file(music_id, quality, cookies=…)`：内部先查缓存键 `(song_id, quality)` 并做 SHA-256 完整性校验，通过则复用文件并经 root.db 组装响应（`cached=true`，凭据与上游均不使用）；校验不通过判定损坏，删除重下并重新登记哈希；未命中才发起唯一一轮上游调用（URL/详情/歌词一次取齐，携带请求级凭据）并落盘登记；
4. `DownloadException` 返回 404（版权限制/取链失败等）；其他下载失败返回 500；
5. 命中缓存时执行 `storage.record_access(song_id)` 累加访问量；
6. `format=json` 返回下载信息（含 `cached` 字段）；`format=file` 用 `FileResponse(filename=…)` 返回文件流，附 `X-Download-Message`、`X-Download-Filename`（实际文件名，`quote` 编码）响应头；
7. 真实完成交付后 `session_manager.record_download(request.state.session_id, song_id, quality)` 计入排行榜（同会话同歌曲去重）。

### 6.3 `/recommend` 实现（2026-11-15 新增）

1. 校验 `song_id` 非空并可解析为数字 ID（`_extract_music_id` 支持链接/短链）；
2. 委托 `StorageManager.recommend_for(song_id, limit=5)`（打分/兜底算法见 `docs/api.md` 第 4.9 节）；
3. 返回 `{song_id, size, recommendations[]}`，每项含 `qualities`（实际缓存音质版本 + 文件大小，`cached=true`）。

## 7. 启动流程（`start.py`，统一启动入口）

1. 依次执行四项启动检查并逐行输出结果：依赖安装 → 配置文件存在/可解析 → 配置项合法性 → 端口占用（socket 试绑定）；任一失败输出汇总后以退出码 1 终止；
2. 全部通过后才 `import main` 初始化服务（避免带病初始化）；
3. 打印启动横幅（服务地址、存储目录、端点清单、音质列表、限速阈值），`uvicorn.run(main.app, …, access_log=False)` 启动 ASGI 服务（access log 关闭，请求行由统一日志单点输出）；`KeyboardInterrupt` 优雅退出。

`main.py` 不含 `__main__` 入口，被导入（如测试）只完成应用与服务初始化。

## 8. 修改注意事项

1. 大写别名路由已移除（2026-11-15），新增路由只注册小写路径；接口变化须同步 `docs/api.md` 与前端调用（`docs/modules/frontend.md`）。
2. 新增音质时需同步：`/song` 白名单、`/download` 白名单、`_get_quality_display_name`、`/api/info` 列表、启动横幅、两个模块的 `QualityLevel` 枚举、前端下拉框。
3. 上游调用一律经 `music_api` 封装，路由层不得直接 `requests` 上游接口。
4. 响应结构变化属破坏性接口变更，须评估 `templates/index.html` 的字段消费。
5. 给新业务路由加限速：叠加 `@limiter.limit(RATE_MINUTE)` 与 `@limiter.limit(RATE_HOUR)`，并确保路由签名含 `request: Request`（slowapi 依赖它取来源 IP）。
