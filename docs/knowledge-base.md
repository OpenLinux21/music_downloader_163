# 项目知识库

> 本文档是项目的持续维护知识库，收录经代码实证的事实、外部接口事实、已知问题与待确认事项。
> 任何 Agent 在开发过程中发现新的可验证事实、解决或新增已知问题，都必须同步更新本文档（见 `docs/doc-maintenance.md`）。

## 1. 项目基本信息

| 项 | 内容 |
| --- | --- |
| 项目名称 | 网易云音乐解析/下载服务（网易云音乐工具箱） |
| 上游来源 | Fork 自 `Suxiaoqinx/Netease_url`（MIT License） |
| 本仓库远程 | `git@github.com:OpenLinux21/music_downloader_163.git` |
| 默认分支 | `main`（历史开发分支）；`develop` 为日常开发基准分支（2026-11-15 由用户确认后自 main 创建） |
| 运行版本 | Python 3.12（本机 `.venv` 实测 3.12.16；Docker `python:3.12-alpine3.23`） |
| 启动方式 | `python start.py`（统一启动入口：依赖/配置文件/配置项/端口四项自检通过后才启动服务） |
| 配置文件 | `config.toml`（唯一配置来源，Git 不跟踪）+ 提交模板 `config.example.toml`；读取模块 `config_loader.py` |
| 数据库 | sqlite3：`storage/root.db`（songs/files 表，无 ORM；设计见 `docs/database.md`） |
| 测试框架 | 无；`tests/` 下有断言式自检脚本（直接运行）；标准验证方式见 `docs/doc-maintenance.md` 第 3 节 |

## 2. 术语表

| 术语 | 含义 |
| --- | --- |
| eapi | 网易云客户端加密接口协议：请求路径 `/eapi/xxx`，参数经特定拼接后 AES-ECB 加密为 `params` 字段 POST |
| MUSIC_U | 网易云登录态 Cookie 字段，服务鉴权的核心凭据 |
| 音质等级 | `standard`/`exhigh`/`lossless`/`hires`/`jyeffect`/`sky`/`jymaster`/`dolby` 共 8 级（代码实证，见 `docs/api.md`） |
| 黑胶 VIP / SVIP | 网易云会员等级；README 注明 `sky`、`jymaster` 需要 SVIP（此说法来自 README，代码层面未校验，属上游权限行为） |
| 兼容路由 | 同一视图函数注册的多个路径别名，如 `/song` 与 `/Song_V1`、`/search` 与 `/Search` 等 |
|杜比全景声 | `dolby` 音质，上游返回 mp4 容器（EAC3），下载器单独处理（`+faststart`） |

## 3. 实证事实清单（代码级验证）

以下事实均通过阅读当前代码与本地运行验证得出，修改对应代码时必须复查：

1. **启动副作用**：导入 `main.py` 即实例化 `MusicAPIService`，会创建 `downloads/` 与 `logs/` 目录，并初始化统一日志（`SERVER` 组件，控制台彩色 + `logs/service.log` 轮转；2026-11-15 起替代原「控制台 + music_api.log 双写」方案，`music_api.log` 不再生成）。
2. **Cookie 即时读取**：每个业务请求处理时都调用 `load_cookies()`，后者经 `config_loader.get_default_cookie()` 实时读取 `config.toml` 的 `server.default_cookie`（原 `cookie.txt` 已于 2026-11-15 废弃并从 Git 移除），无内存缓存；未配置或配置文件缺失返回空字典且不抛异常；配置文件存在但 TOML 解析失败时异常上抛（导入 `main.py` 阶段即失败）。
3. **Cookie 状态判定**：`/health` 的 `cookie_status` 仅判断 Cookie 字典是否非空（`valid`/`invalid`），不校验登录态是否真实有效。
4. **音质校验表**：`/song` 与 `/download` 的合法音质列表为 8 项（含 `dolby`），硬编码在 `main.py` 的两个路由函数内；`/api/info` 返回的 `supported_qualities` 只有 7 项（缺 `dolby`），属文档性字段不一致。
5. **eapi 加密算法**（`music_api.CryptoUtils.encrypt_params`）：
   - 取 URL path 并把 `/eapi/` 替换为 `/api/`；
   - `digest = md5("nobody" + path + "use" + json(payload) + "md5forencrypt")`；
   - 明文 `params = path + "-36cd479b6b5-" + json(payload) + "-36cd479b6b5-" + digest`；
   - 以固定密钥 `e82ckenh8dichen8` 做 AES-ECB + PKCS7 加密后转 hex。
6. **请求头伪装**：eapi 请求使用 `NeteaseMusicDesktop/2.10.2` 桌面客户端 UA；`DEFAULT_COOKIES` 注入 `os=pc; deviceId=pyncm!` 等默认 Cookie 后再合并用户 Cookie（用户 Cookie 优先）。
7. **dolby 编码类型**：请求 dolby 音质时 `encodeType` 为 `mp4`，其余为 `flac`；`sky` 音质额外携带 `immerseType=c51`。
8. **歌曲详情与搜索不走 eapi**：`get_song_detail`、`get_lyric`、`search_music`、`get_playlist_detail`、`get_album_detail` 均为普通 HTTP 请求（其中 `get_song_detail` 不携带 UA/Referer 头）。
9. **封面直链算法**：`netease_encrypt_id` 使用魔串 `3go8&$8*3*3h0k(2)2` 异或 + MD5 + Base64（`/`→`_`、`+`→`-`）生成 `p3.music.126.net` 图片直链。
10. **歌单详情分批**：先请求 `api/v6/playlist/detail` 拿全部 `trackIds`，再按每批 100 个调用 `api/v3/song/detail` 补全歌曲信息。
11. **搜索参数限制**：`/search` 的 `limit` 上限 100（超出被截断），默认 30；`offset` 与 `type` 参数被读取但**未传递**给上游（上游 `type` 硬编码为 `1` 仅搜歌曲）。
12. **下载文件命名（2026-11-15 缓存键化后）**：实际存储路径为 `storage/<song_id>/歌曲名.扩展名`（经非法字符清洗、200 字符截断）；同一扩展名被另一音质占用时追加音质段（`歌曲名.<quality>.扩展名`）；原 `downloads/歌手 - 歌名.扩展名` 与响应展示名 `歌名 [音质]` 已废除。
13. **扩展名判定优先级**：API 返回 `type` > URL 后缀 > HTTP Content-Type > 默认 `.mp3`（不变，落盘路径随缓存键化调整）。
14. **ffmpeg 懒加载**：启动时 `shutil.which("ffmpeg")` 检测一次；每次写元数据前再检测一次并回写缓存，因此服务运行中途安装 ffmpeg 也可生效。
15. **元数据失败不阻断**：ffmpeg 写元数据失败只打印警告并返回 `False`，不影响下载结果；成功后用临时文件 `*.tagging.*` 原子替换原文件。
16. **二维码登录双实现**：`music_api.QRLoginManager.qr_login()` 为可用实现（终端 ASCII 二维码，轮询 801/802/803 状态码）；`qr_login.py` 的 `QRLoginClient` 提供交互式 CLI，但与现行接口不匹配（见已知问题 #1）。
17. **Cookie 有效性口径**（`qr_login.py`）：包含 `MUSIC_U`、`__csrf`、`NMTID` 任一字段即视为有效；保存前先备份旧文件。
18. **前端接口调用**（`templates/index.html`）：搜索 POST `/Search`、解析 POST `/Song_V1`（`type=json`）、歌单 GET `/Playlist`、专辑 GET `/Album`、下载 XHR POST `/Download`（blob 响应，读取 `X-Download-Message`/`X-Download-Filename` 响应头）。
19. **CORS 全开放**：`after_request` 对所有响应添加 `Access-Control-Allow-Origin: *` 等 4 个 CORS 头。
20. **容器时区**：Dockerfile 设置 `TZ=Asia/Shanghai`；pip 使用阿里云镜像源。
21. **配置加载行为**（`config_loader.py`）：`load_config()` 严格读取（文件缺失抛 `FileNotFoundError`、非法 TOML 抛 `tomllib.TOMLDecodeError`）；`get_config()` 在文件缺失时回落内置默认值并向 stderr 告警，解析错误不吞直接抛出；所有读取无内存缓存，修改配置即时生效。内置默认值：host `127.0.0.1`、port `5000`、`max_size_mb` 8192（MB）、`multi_thread` false、`speed_limit_kb` 0（KB/s，0 不限速）、限速 10 次/分钟 + 25 次/小时。`main.py` 在模块导入时把 `server.host`/`server.port` 接线到 `APIConfig`。
22. **Cookie 写回与备份**（`config_loader.save_default_cookie` / `qr_login.py`）：写回采用文本替换只改动 `default_cookie` 一行（值经 `json.dumps` 编码，转义规则与 TOML 基本字符串兼容），缺键时插入 `[server]` 节头之后、缺节时文件末尾追加、文件缺失时自动创建最小配置；`qr_login.py` 覆盖/清除前把旧 Cookie 备份为项目根 `cookie.backup.<suffix>.<时间戳>.txt`（已 gitignore），`logout` 通过写空串清除而非删除文件。
23. **统一日志系统**（`logger.py`，2026-11-15 落地）：GIN 风格格式 `[组件] YYYY/MM/DD - hh:mm:ss | 状态码 | 微秒耗时(3 位小数) | IP | 接口 [| 消息]`；请求行状态码按区间 ANSI 着色（2xx 绿/3xx 青/4xx 黄/5xx 红），事件按级别映射 OK/WARN/ERROR；控制台 handler 着色、`logs/service.log` handler（5MB 轮转 ×3）不着色；请求行由 `after_request` 输出（`before_request` 仅存起始时间），每请求恰好一条；Werkzeug 自带请求日志被压到 ERROR；组件含 `SERVER`（级别读 `config.log_level`）与 `DOWNLOADER`（库内事件，CLI 交互仍为 print）；日志只记录元信息不记录请求体。
24. **启动自检流程**（`start.py`，2026-11-15 落地）：启动前依次检查 ①依赖安装（`importlib.util.find_spec` 逐个探测 flask/requests/aiohttp/aiofiles/cryptography/qrcode）②配置文件存在且 `tomllib` 可解析（严格读取）③配置项合法性（类型与取值范围，含每小时限速 ≥ 每分钟限速的跨项语义校验；`load_config` 已合并默认值，缺键回落默认）④端口占用（`socket` 试绑定，存在微小 TOCTOU 窗口，仅作预警）；每项结果逐行输出（`[启动检查] 名称 ：通过/失败（原因）`），任一失败以退出码 1 终止且不导入 `main`；全部通过后 `import main` 初始化并 `app.run(threaded=True)`。
25. **存储与缓存体系**（`storage_manager.py`，2026-11-15 落地）：`storage/<song_id>/{音频,cover.jpg,lyric.lrc}` + `albums/<album_id>/<song_id>/` 副本 + `root.db`（songs/files 表）；缓存键 `(song_id, quality)`，命中 = DB 记录 + 文件存在（`is_copy=0`），命中复用不发起上游调用且 `record_access` 累加 `visit_count`；`/download` 路由已删除预调用上游逻辑（每请求上游调用从约 5 次降为命中 0 次 / 未命中 3 次）；清理按歌曲粒度：`created_at` 超 90 天到期删除、总大小超 `cache.max_size_mb` 按 `visit_count` 升序淘汰（并列按 song_id）；DB 为每操作独立短连接，登记顺序保证 files.size 与 ffmpeg 重写后落盘一致；守护线程每 3600 秒清理一次。端到端实测（ID 347230）：首次下载约 2.0s，缓存命中约 2.7ms。

## 4. 外部接口事实（上游依赖）

服务调用的上游接口共 8 个，定义于 `music_api.APIConstants`：

| 常量 | URL | 协议 | 用途 |
| --- | --- | --- | --- |
| `SONG_URL_V1` | `https://interface3.music.163.com/eapi/song/enhance/player/url/v1` | eapi | 获取播放/下载 URL |
| `SONG_DETAIL_V3` | `https://interface3.music.163.com/api/v3/song/detail` | 普通 POST | 歌曲详情（批量） |
| `LYRIC_API` | `https://interface3.music.163.com/api/song/lyric` | 普通 POST | 歌词 |
| `SEARCH_API` | `https://music.163.com/api/cloudsearch/pc` | 普通 POST | 搜索 |
| `PLAYLIST_DETAIL_API` | `https://music.163.com/api/v6/playlist/detail` | 普通 POST | 歌单详情 |
| `ALBUM_DETAIL_API` | `https://music.163.com/api/v1/album/{id}` | 普通 GET | 专辑详情 |
| `QR_UNIKEY_API` | `https://interface3.music.163.com/eapi/login/qrcode/unikey` | eapi | 生成二维码 key |
| `QR_LOGIN_API` | `https://interface3.music.163.com/eapi/login/qrcode/client/login` | eapi | 轮询二维码登录状态 |

统一约束：上游请求超时均为 30 秒；非 200 业务码抛出 `APIException`。上游接口为非公开契约，行为可能随网易云调整而变化，排查解析类故障时应先检查上游返回。

## 5. 已知问题（截至当前代码）

| # | 问题 | 位置 | 影响 | 建议 |
| --- | --- | --- | --- | --- |
| 1 | `qr_login.py` 与 `music_api.py` 接口不匹配：`QRLoginClient.interactive_login()` 期望 `create_qr_login()` 返回含 `success`/`qr_key` 的字典、`check_qr_login()` 返回含 `success`/`status` 的字典，但现行实现分别返回 `Optional[str]` 和 `Tuple[int, dict]`，访问 `.get()` 会抛 `AttributeError`，`login` 命令实际不可用 | `qr_login.py` 的 `interactive_login()`；`music_api.py` 的 `QRLoginManager` | CLI 登录不可用；`music_api.qr_login()` 可正常使用 | 修复时需同步 `docs/modules/netease-api.md` |
| 2 | ~~`entrypoint.sh` 向 `main.py` 传 `--level/--mode/--url` 参数，但 `main.py` 无 argparse，参数被忽略；`.env` 中 `LEVEL/MODE/URL` 均无效~~ **已修复（2026-11-15）**：`entrypoint.sh` 改为生成默认配置后调用 `python3 start.py`，无效参数与 `.env` 文件已删除 | `entrypoint.sh`、`.env`（已删）、`start.py` | （已消除无效配置通道） | 已同步 `docs/deployment.md` |
| 3 | ~~`/download` 路由的缓存命中检查文件名为 `歌名 [音质].扩展名`，与下载器实际保存名 `歌手 - 歌名.扩展名` 不一致，检查基本不生效，导致重复下载~~ **已修复（2026-11-15）**：缓存键改为 `(song_id, quality)`（root.db `files` 表），命中判定与文件名解耦 | `main.py` 的 `download_music_api()`、`storage_manager.py` | （重复下载浪费带宽问题消除） | 已同步 `docs/api.md`、`docs/database.md` |
| 4 | `/api/info` 返回的 `supported_qualities` 缺少 `dolby`，与 `/song`、`/download` 的校验列表（8 项）不一致 | `main.py` 的 `api_info()` | 信息展示不完整 | 修复时需同步 `docs/api.md` |
| 5 | ~~`/download` 链路重复调用上游：`main.py` 先调 `name_v1`+`url_v1`，随后 `MusicDownloader.get_music_info()` 内部再调一次 URL、详情、歌词接口~~ **已修复（2026-11-15）**：路由删除预调用，链路收敛为缓存命中 0 次 / 未命中 1 轮（URL+详情+歌词） | `main.py` 的 `download_music_api()`、`music_downloader.py` | （每次下载约 5 次上游请求降为最多 3 次） | 已同步 `docs/data-flow.md` |
| 6 | ~~`music_downloader.get_music_info()` 中存在调试残留 `print(url_result)`，会把含下载 URL 的完整响应打印到控制台~~ **已修复（2026-11-15）**：随统一日志任务移除，库内事件改经 `DOWNLOADER` 组件日志器输出 | `music_downloader.py` 的 `get_music_info()` | （已消除日志噪音与 URL 泄露） | 已同步 `docs/modules/downloader.md` |
| 7 | ~~`downloads/` 目录未被 `.gitignore` 覆盖，下载产物有被误提交的风险~~ **已修复（2026-11-15）**：`.gitignore` 已覆盖 `downloads/` 与 `storage/`（含 root.db 等运行产物） | `.gitignore` | （仓库卫生风险消除） | 提交前仍须检查暂存区（AGENTS.md 凭据红线） |
| 8 | 前端错误分支访问 `data.data.msg`，而错误响应结构中不存在 `data` 字段（`APIResponse.error` 仅含 `status/success/message`），会抛 `TypeError`，错误提示无法显示 | `templates/index.html` 各 `$.post/$.get` 的失败分支 | 用户看不到具体错误原因 | 修复时需同步 `docs/modules/frontend.md` |
| 9 | `music_api.py` 与 `music_downloader.py` 重复定义 `QualityLevel` 枚举，且两处均未被业务逻辑实际引用（音质校验用硬编码列表） | 两个模块顶部 | 修改音质列表需多处同步 | 见 `docs/doc-maintenance.md` |
| 10 | `main.py` 导入的 `QualityLevel`、`AudioFormat` 实际未使用 | `main.py` 顶部 import | 轻微冗余 | 可在重构任务中清理 |

## 6. 待确认事项

| # | 事项 | 状态 |
| --- | --- | --- |
| 1 | `cookie.txt` 中凭据的性质 | **已确认关闭**：用户明确说明该文件内容为无用的示例凭据，非真实账号 |
| 2 | `.env` 中 `MODE=api`、`URL=http://127.0.0.1:5000` 的原始设计意图（疑似历史 GUI/API 双模式遗留） | **已确认关闭（2026-11-15）**：随入口统一任务，`.env` 及无效参数已整体废除（用户确认的 Docker 同步方案），原始意图不再需要追溯 |
| 3 | Git 历史中 commit `ba92e46 "3"` 的实际内容意图 | 待确认：提交信息无含义，未影响现状理解 |
| 4 | 上游接口对非会员/失效 Cookie 的具体返回行为 | 待确认：需真实账号验证，文档仅记录代码层处理逻辑 |
| 5 | `music_downloader.get_download_progress()` 在文件已存在但经过 ffmpeg 元数据重写（文件大小变化）时进度可能大于 100%（代码用 `min(progress, 100)` 截断显示，`completed` 判断以期望大小为准） | 已按代码记录，实际业务影响待确认 |

## 7. 变更记录

| 日期 | 变更 | 执行者 |
| --- | --- | --- |
| 2026-11-15 | 初版创建：完成全项目勘察，登记 10 项已知问题、5 项待确认事项 | AI Agent（文档体系建设任务） |
| 2026-11-15 | 配置系统落地（feat/config-system）：新增 `config.toml`/`config.example.toml`/`config_loader.py`；Cookie 自 `cookie.txt` 迁入 `server.default_cookie` 并废弃 `cookie.txt`（`git rm` + .gitignore/.dockerignore 排除）；`main.py` 绑定地址/端口接入配置；`qr_login.py` Cookie 存取改走 config.toml；实证事实 2 改写，新增事实 21/22；新增自检脚本 `tests/test_config_system.py` | AI Agent |
| 2026-11-15 | 统一日志系统落地（feat/logging-system）：新增 `logger.py`（GIN 风格，控制台彩色 + `logs/service.log` 5MB 轮转），废除 music_api.log 双写；请求行由 after_request 单点输出；DOWNLOADER 组件接入（已知问题 #6 修复）；实证事实 1 改写、新增事实 23；新增自检脚本 `tests/test_logging_system.py` | AI Agent |
| 2026-11-15 | 统一启动入口落地（feat/start-entry）：新增 `start.py`（四项自检逐行输出）；`main.py` 移除启动逻辑；`entrypoint.sh` 改调 start.py，删除 `.env` 与无效参数（已知问题 #2 修复、待确认 #2 关闭）；AGENTS.md 第 1 节运行入口经用户授权修订；新增事实 24；新增自检脚本 `tests/test_start_checks.py`（含真实启动端到端） | AI Agent |
| 2026-11-15 | 存储与缓存落地（feat/storage-cache）：新增 `storage_manager.py` 与 `storage/root.db`（songs/files），缓存键 `(song_id, quality)`，命中复用+访问计数，3 个月到期/超限按访问量淘汰双策略，专辑副本另存；`/download` 路由重写（已知问题 #3/#5/#7 修复）；新增事实 25、改写事实 12；新建 `docs/database.md` 并登记；新增自检脚本 `tests/test_storage_cache.py`（含端到端真实下载验证，用户已授权） | AI Agent |
