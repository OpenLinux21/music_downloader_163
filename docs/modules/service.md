# 模块详解：Web 服务层（main.py）

> 本文档详解 `main.py` 的内部实现。修改该文件前须先读取 `docs/skills/!dev.md` 与本文档；涉及对外接口变化时还须同步 `docs/api.md` 与 `docs/modules/frontend.md`（映射规则见 `docs/doc-maintenance.md`）。
> 接口的对外行为（参数/响应/错误码）以 `docs/api.md` 为准，本文聚焦内部实现。代码变化时须按 `docs/doc-maintenance.md` 同步更新。

## 1. 模块职责

- 定义 Flask 应用与全部 HTTP 路由（唯一路由层；服务启动入口为 `start.py`）；
- 参数校验、ID/链接解析、上游调用编排、响应格式统一；
- 初始化 `SERVER` 统一日志器（GIN 风格，见 `logger.py`）与 CORS 注入；
- 不再包含启动逻辑（原 `start_api_server()` 已随入口统一移入 `start.py`）。

## 2. 配置类 `APIConfig`（dataclass）

| 字段 | 默认值 | 说明 |
| --- | --- | --- |
| `host` | `127.0.0.1`（实例化时读自 `config.toml` 的 `server.host`） | 监听地址 |
| `port` | `5000`（实例化时读自 `config.toml` 的 `server.port`） | 监听端口 |
| `debug` | `False` | Flask 调试模式 |
| `storage_dir` | `storage` | 存储根目录（相对项目根，按歌曲 ID 分类） |
| `max_cache_mb` | `8192`（实例化时读自 `config.toml` 的 `cache.max_size_mb`） | 本地缓存总大小上限（MB），超限按访问量淘汰 |
| `multi_thread` | `False`（读自 `config.toml` 的 `download.multi_thread`） | 单文件多线程（Range 分段）下载开关，不支持 Range 自动回退单流 |
| `speed_limit_kb` | `0`（读自 `config.toml` 的 `download.speed_limit_kb`） | 下载限速（KB/s），0 不限速 |
| `max_file_size` | 500MB | 仅在 `/api/info` 中展示，**无实际限制逻辑** |
| `request_timeout` | 30 | 仅在 `/api/info` 中展示，**未传递给上游调用**（上游超时硬编码在 `music_api`） |
| `log_level` | `INFO` | 统一日志级别（`SERVER` 组件，经 `logger.get_logger` 生效） |
| `cors_origins` | `*` | CORS 允许来源 |

注意：`max_file_size` 与 `request_timeout` 是展示性配置，代码中无对应执行逻辑，调整它们不会改变行为。`host`/`port` 的 dataclass 默认值仅在 `config.toml` 缺失对应键时生效；正常运行取值来自 `config.toml`。

## 3. 响应工具 `APIResponse`

- `success(data, message, status_code=200)`：返回 `(dict, int)` 二元组，结构 `{status, success, message, data?}`；
- `error(message, status_code=400, error_code=None)`：结构 `{status, success, message, error_code?}`，当前所有调用均不传 `error_code`。

所有路由视图都返回该结构，是前后端契约的基座，改动属接口破坏性变更。

## 4. 服务类 `MusicAPIService`

实例化时机：**模块导入时**（`api_service = MusicAPIService(config)`），即 `import main` 就会产生副作用：初始化 `NeteaseAPI`、创建 `StorageManager`（建 `storage/` 与 `root.db` 并立即执行一轮清理）、启动清理守护线程、初始化 `MusicDownloader`（注入同一存储管理器）、初始化 `SERVER` 统一日志器。

关键内部方法：

| 方法 | 行为要点 |
| --- | --- |
| 日志 | `get_logger("SERVER", config.log_level)`：GIN 风格统一日志（`logger.py`），控制台 ANSI 着色 + `logs/service.log` 5MB 轮转 ×3（无着色） |
| `_get_cookies` | 调 `load_cookies()` 每请求实时读 `config.toml` 的 `server.default_cookie`；未配置打 warning 返回 `{}`；异常返回 `{}` 不中断 |
| `_extract_music_id` | 短链 `163cn.tv` 展开（不跟随重定向取 Location）→ `music.163.com` 提取 `id=` → 否则原样 strip；任何异常返回原串 |
| `_format_file_size` | B/KB/MB/GB/TB 自动换算，两位小数 |
| `_get_quality_display_name` | 8 种音质的中文名映射，未知名返回 `未知音质(xxx)` |
| `_validate_request_params` | 任一参数为假值即返回 400 错误响应 |
| `_safe_get_request_data` | GET 取 query；POST 合并表单与 JSON（JSON 优先） |

## 5. 全局钩子与错误处理

| 钩子 | 行为 |
| --- | --- |
| `before_request` | `request.environ` 记录 `perf_counter()` 起始时间（供请求行耗时统计） |
| `after_request` | 追加 4 个 CORS 头（Allow-Origin `*`、Allow-Headers、Allow-Methods `GET,POST,OPTIONS`、Max-Age 3600）；输出单条 GIN 风格请求行（`[SERVER] 时间 \| 状态码 \| 微秒耗时 \| IP \| METHOD 路径`） |
| `errorhandler(400/404/500)` | 统一转为 `APIResponse.error` 文案（「请求参数错误」「请求的资源不存在」「服务器内部错误」） |

## 6. 路由清单与实现要点

| 路由（含别名） | 视图函数 | 实现要点 |
| --- | --- | --- |
| `GET /` | `index` | `render_template('index.html')` |
| `GET /health` | `health_check` | 重读 Cookie 判 `valid/invalid`（仅判非空）；返回版本 2.0.0、下载目录绝对路径 |
| `GET|POST /song`、`/Song_V1` | `get_song_info` | 见下文 6.1 |
| `GET|POST /search`、`/Search` | `search_music_api` | `keyword/keywords/q` 三参兼容；`limit` 上限 100；`offset/type` 读取但未使用；结果每项追加 `artist_string`（与 `artists` 同值） |
| `GET|POST /playlist`、`/Playlist` | `get_playlist` | 调 `playlist_detail` 后包装为 `data={status:'success', playlist:结果}`；**id 不做链接解析** |
| `GET|POST /album`、`/Album` | `get_album` | 调 `album_detail` 后包装为 `data={status:200, album:结果}`；**id 不做链接解析** |
| `GET|POST /download`、`/Download` | `download_music_api` | 见下文 6.2 |
| `GET /api/info` | `api_info` | 服务元信息；`supported_qualities` 硬编码 7 项缺 `dolby`（已知问题 #4） |

### 6.1 `/song` 实现

1. 校验 `level` ∈ 8 项白名单（含 `dolby`）、`type` ∈ `url/name/lyric/json`；
2. `type=url`：`url_v1` 成功后取 `data[0]` 组装响应（含 `size_formatted`、`quality_name`）；空数据返回 404；
3. `type=name`/`lyric`：直接透传上游结构；
4. `type=json`：并发语义上是顺序调用 `name_v1` + `url_v1` + `lyric_v1` 三次上游，聚合为前端解析页所需结构；URL 失败不报错（`url=''`、`size='获取失败'`）。

### 6.2 `/download` 实现（2026-11-15 重写）

1. 校验 `quality`、`format`；
2. 读取可选 `user_cookie`：内存中 `parse_cookie_string` 解析为请求级凭据（无则 `None`，上游回落 config 默认 Cookie）；凭据不落盘、不写日志、不写 DB，请求结束随局部变量丢弃；
3. 委托 `downloader.download_music_file(music_id, quality, cookies=…)`：内部先查缓存键 `(song_id, quality)` 并做 SHA-256 完整性校验，通过则复用文件并经 root.db 组装响应（`cached=true`，凭据与上游均不使用）；校验不通过判定损坏，删除重下并重新登记哈希；未命中才发起唯一一轮上游调用（URL/详情/歌词一次取齐，携带请求级凭据）并落盘登记（原路由层 `name_v1`+`url_v1` 预调用已删除，知识库已知问题 #5 修复）；
4. `DownloadException` 返回 404（版权限制/取链失败等）；其他下载失败返回 500；
5. 命中缓存时执行 `storage.record_access(song_id)` 累加访问量；
6. `format=json` 返回下载信息（含 `cached` 字段）；`format=file` 用 `send_file(as_attachment=True)` 返回文件流，附 `X-Download-Message`、`X-Download-Filename`（实际文件名，`quote` 编码）响应头。

## 7. 启动流程（`start.py`，统一启动入口）

1. 依次执行四项启动检查并逐行输出结果：依赖安装 → 配置文件存在/可解析 → 配置项合法性 → 端口占用（socket 试绑定）；任一失败输出汇总后以退出码 1 终止；
2. 全部通过后才 `import main` 初始化服务（避免带病初始化），并压制 Werkzeug 自带请求日志；
3. 打印启动横幅（服务地址、下载目录、端点清单、音质列表），`app.run(host, port, debug=False, threaded=True)` 启动开发服务器；`KeyboardInterrupt` 优雅退出。

`main.py` 不含 `__main__` 入口，被导入（如测试）只完成应用与服务初始化。

## 8. 修改注意事项

1. 路由改名/删除必须同时保留大写别名兼容模式，并同步 `docs/api.md` 与前端调用（`docs/modules/frontend.md`）。
2. 新增音质时需同步：`/song` 白名单、`/download` 白名单、`_get_quality_display_name`、`/api/info` 列表、启动横幅、两个模块的 `QualityLevel` 枚举、前端下拉框。
3. 上游调用一律经 `music_api` 封装，路由层不得直接 `requests` 上游接口。
4. 响应结构变化属破坏性接口变更，须评估 `templates/index.html` 的字段消费。
