# 开发历史与重要设计决策

> 本文档基于 `git log` 与代码现状归纳项目的演进脉络和关键设计决策，用于帮助后续开发者理解「为什么是现在这个样子」。
> 归纳基于提交信息与代码比对，个别提交信息本身无明确含义（已标注）；原始历史以 `git log` 为准。

## 1. 演进时间线

| 阶段 | 提交（节选） | 内容 |
| --- | --- | --- |
| 单文件原型 | `d1e1327` 添加GUI模式解析、`2d61912`~`ccae60b` | 早期为单文件脚本与 GUI/网页解析；PR#14 引入「bypass CROS」处理（CORS 兼容的前身） |
| 功能扩展 | `9ca4bda` 添加歌单/搜索/专辑解析、`7ee2b61`、`134543d` | 在原型上补齐歌单、搜索、专辑能力与前端页面 |
| 模块化拆分 | `d93db93` 修改为模块化 单独分离API | **关键重构**：API 封装独立为 `music_api.py`，形成当前三模块结构 |
| 登录能力 | `39ce4e9` 添加二维码登录接口、`6e7c50c` | 引入二维码登录（`QRLoginManager` 与 `qr_login.py` CLI） |
| 下载能力 | `45056e9` 新增音乐文件下载功能-包含音乐元信息、PR#24 `5af0d3d` | 新增下载器与 ffmpeg 元数据写入；修复缺失的 `time` 导入 |
| Cookie 管理重构 + 杜比 | `3b3383c` feat: 新增杜比全景声支持，重构Cookie管理与项目配置、`e584e72` 等 | **关键重构**：Cookie 改为直读 `cookie.txt`（`load_cookies`/`parse_cookie_string`），新增 `dolby` 音质与 MP4(EAC3) 下载分支 |
| Docker 化 | PR#15 `d5348c3`/`60e3270` 新增 docker/.gitignore | 引入 Dockerfile、docker-compose、entrypoint 与 .env |
| Docker 修复 | PR#133 `fcbb409` align Docker Python version、`0e3af03`、`3408787` | 将镜像 Python 版本与锁定依赖对齐（修复构建兼容性）；移除 Docker 冒烟 CI；**镜像定为 python:3.12-alpine3.23** |
| 维护 | `18b28be` chore: ignore music_api.log | 将运行日志纳入 .gitignore |
| 配置系统 | `feat/config-system` 分支（2026-11-15） | 引入 `config.toml` 配置体系与 `config_loader.py`；Cookie 自 `cookie.txt` 迁入配置并废弃原文件 |
| 统一日志 | `feat/logging-system` 分支（2026-11-15） | 新增 `logger.py`（GIN 风格请求行/事件行），废除 `music_api.log` 双写 |
| 统一启动入口 | `feat/start-entry` 分支（2026-11-15） | 新增 `start.py` 四项自检入口；废除 `.env` 与无效启动参数；AGENTS.md 入口声明经用户授权修订 |
| 存储与缓存 | `feat/storage-cache` 分支（2026-11-15） | `storage/` 目录体系 + `root.db`；缓存键 `(song_id, quality)` 与双策略清理；修复已知问题 #3/#5/#7 |
| 哈希追踪 | `feat/sha256-tracking` 分支（2026-11-15） | `sha256sum.db` 全量文件哈希登记 + 命中前校验 + 损坏失效重下链路 |
| 下载与元数据 | `feat/download-metadata` 分支（2026-11-15） | 歌词嵌入（三格式实测）+ 多线程 Range 下载（自动回退）+ KB/s 限速 |
| 用户自有凭据 | `feat/user-cookie` 分支（2026-11-15） | `/download` 请求级凭据（localStorage + 内存使用零持久化）+ 前端 Cookie 入口 |
| FastAPI 迁移 | `feat/fastapi-migration` 分支（2026-11-15） | Flask → FastAPI（uvicorn）；大写别名移除；slowapi 限速；依赖清单重构 |
| 排行榜与防刷 | `feat/ranking-antispam` 分支（2026-11-15） | HMAC 防刷会话 Cookie + cookies.db 交付去重 + `/rank` TOP20 排行榜 |
| 智能推荐 | `feat/smart-recommend` 分支（2026-11-16） | `/recommend` 特征打分推荐（同歌手/近似名/同专辑/热门兜底）+ 前端推荐区块 |

## 2. 重要设计决策及动机（依据代码现状推断与提交记录）

### 2.1 模块化三层结构

决策：`main.py`（路由/编排）、`music_api.py`（上游协议封装）、`music_downloader.py`（文件下载与后处理）三层分离，另加独立 CLI `qr_login.py`。
动机：`d93db93` 提交明确为「模块化、单独分离 API」，将协议细节（加密、请求头）与 Web 编排解耦。
现状代价：`music_downloader` 内部又实例化一份 `NeteaseAPI`，导致 `/download` 链路重复调用上游（知识库已知问题 #5）。

### 2.2 eapi 加密直连官方客户端接口

决策：不依赖第三方 NeteaseCloudMusicApi 服务，用 `cryptography` 在进程内实现 eapi 参数加密（固定密钥 AES-ECB），并以桌面客户端 UA + `os=pc` Cookie 伪装客户端请求。
收益：单进程自包含，便于 Docker 化。
约束：上游为非公开契约，接口路径/加密流程变化会直接破坏解析能力（排查思路见 `docs/knowledge-base.md` 第 4 节）。

### 2.3 Cookie 直读文件、每请求加载

决策：`3b3383c` 重构后，Cookie 不再走配置对象，而是每次请求从 `cookie.txt` 解析为字典；文件缺失/损坏返回空字典不抛错。
收益：更换账号零重启；文件即状态，运维直观。
代价：每请求一次磁盘 IO（个人规模可忽略）；Cookie 是否「有效」只能靠调用结果判断。

### 2.4 大写别名兼容路由

决策：每个业务路由同时注册 `/song` 与 `/Song_V1`、`/search` 与 `/Search` 等，视图函数共用。
动机：兼容早期已对外发布的旧路径。
影响：新增接口时应遵循同一模式（见 `docs/modules/service.md` 第 8 节修改注意事项）。

### 2.5 下载后处理用外部 ffmpeg

决策：元数据/封面写入依赖系统 `ffmpeg` 二进制（非 Python 库），按扩展名分支构造参数，杜比 MP4 启用 `+faststart`；写入失败不影响下载结果。
细节设计：临时输出 `*.tagging.*` 保留原扩展名便于 muxer 识别，成功后原子替换；ffmpeg 路径懒加载，支持运行中途安装。

### 2.6 前端单文件 + CDN

决策：`templates/index.html` 单文件承载全部交互（jQuery + Bootstrap + APlayer，CDN 引入），后端为其增加 `type=json` 聚合接口与 blob 下载响应头（`X-Download-Filename` 等）。
代价：前端依赖外网 CDN；错误分支存在 `data.data.msg` 缺陷（知识库已知问题 #8）。

### 2.7 Docker 镜像锁定 Python 3.12-alpine

决策：PR#133 将镜像与依赖锁定对齐（`python:3.12-alpine3.23`），随后移除 Docker 冒烟 CI（`3408787`）。
动机：修复依赖与镜像 Python 版本不匹配的构建问题；CI 移除原因提交信息未说明（不深究）。

### 2.8 配置体系引入与 Cookie 迁移（2026-11-15，feat/config-system）

决策：以 `config.toml` 作为唯一配置来源（标准库 `tomllib` 解析，不引入第三方 TOML 依赖），`config_loader.py` 提供默认值合并与读写；服务器默认 Cookie 从 `cookie.txt` 迁入 `server.default_cookie`，`cookie.txt` 从 Git 移除并废弃（.gitignore/.dockerignore 双重排除），`qr_login.py` 的 Cookie 存取改经 `config_loader`。
动机：集中硬编码的服务配置（绑定地址、端口、缓存上限、下载开关、限速），为后续启动检查、限速、缓存清理等功能提供配置基础；同时消除凭据文件被 Git 跟踪的安全隐患（AGENTS.md 凭据红线）。
取舍：`tomllib` 只读，Cookie 写回用「文本替换单行 + 缺键插入/缺节追加」实现，保留文件注释与结构（完整 TOML 序列化需第三方库，收益不匹配）；配置读取不做内存缓存，保持与旧 `cookie.txt` 相同的「修改即时生效」语义。模板 `config.example.toml` 提交、真实 `config.toml` 不提交。
迁移效果：本地默认监听地址由 `0.0.0.0` 变为 `127.0.0.1`（容器部署需在挂载的 config.toml 中设 `host = "0.0.0.0"`，见 `docs/deployment.md` §3.4）。

### 2.9 统一日志系统（2026-11-15，feat/logging-system）

决策：以自研 `logger.py`（标准库 `logging` + 自定义 formatter）替换「logging 控制台 + `music_api.log` 文件双写」方案；每请求仅由 `after_request` 输出一条 GIN 风格请求行（`before_request` 只存起始时间），普通事件按级别映射 OK/WARN/ERROR。
取舍：用户要求"控制台 + 文件各一份"，文件侧用 `RotatingFileHandler`（5MB × 3）防无限增长，且文件内容剥离 ANSI 码（避免污染终端之外的消费场景）；`qr_login.py` 为独立交互 CLI，保持自带 logging/print，不纳入统一日志（避免破坏 CLI 交互界面）。
附带修复：移除 `music_downloader.get_music_info()` 的调试残留 `print(url_result)`（知识库已知问题 #6），下载器库内事件全部改经 `DOWNLOADER` 组件日志器。

### 2.10 统一启动入口 start.py（2026-11-15，feat/start-entry）

决策：`start.py` 成为唯一启动入口，启动前依次执行依赖安装、配置文件存在与可解析、配置项合法性、端口占用四项检查并逐行输出结果，全部通过后才 `import main` 初始化服务；`main.py` 移除 `start_api_server()` 与 `__main__` 块，只保留应用定义。容器侧 `entrypoint.sh` 改为「未挂载配置时生成默认 config.toml（host 置 0.0.0.0）→ `python3 start.py`」，删除 `.env` 与无效的 `--level/--mode/--url` 参数（知识库已知问题 #2 修复，待确认事项 #2 一并关闭）。
动机：配置体系落地后需要统一的启动前校验点，把配置错误、端口冲突等问题拦截在服务初始化之前；同时消除从未生效的参数/环境变量通道。
授权说明：AGENTS.md 第 1 节「运行入口」声明属固定内容，本次经用户明确确认后修订为 `python start.py`。
取舍：端口检查用 `socket` 试绑定存在微小 TOCTOU 窗口（检查后、绑定前被抢占），定位为启动预警而非强互斥，真实的绑定冲突仍由 `app.run` 阶段的 `OSError` 兜底。

### 2.11 存储体系与缓存键化（2026-11-15，feat/storage-cache）

决策：以 `storage/<song_id>/` 为单曲资源单元（音频 + cover.jpg + lyric.lrc），归属专辑另存 `albums/<album_id>/<song_id>/` 副本；引入 sqlite3 `root.db`（songs/files 表，标准库无 ORM），缓存键从「文件名猜测」改为 `(song_id, quality)`；`/download` 路由删除预调用上游逻辑，链路收敛为「命中 0 次上游 / 未命中 1 轮」；清理按歌曲粒度双策略（90 天到期 + 超容量按 `visit_count` 升序淘汰）。
动机：原文件名缓存检查失效导致重复下载（已知问题 #3）与每请求约 5 次上游调用（已知问题 #5）是带宽与效率的真实痛点；访问量、清理、后续排行榜/推荐功能都需要结构化元数据支撑。
取舍：sqlite3 短连接换取多线程安全（个人规模无并发瓶颈）；专辑副本以磁盘换可用性（用户明确要求另存，副本计入总量并参与清理）；`files.size` 在 ffmpeg 重写后登记，保证 DB 与落盘一致；`.part` 临时文件 + 原子改名避免半成品被缓存误判。命名采用「歌曲名.扩展名」，扩展名冲突（同扩展名不同音质）时追加音质段，兼容用户对默认命名的示例要求。
迁移说明：历史 `downloads/` 文件不迁移（保留只读遗留），新下载一律进 `storage/`；`docs/database.md` 为随本分支新建的数据库设计文档。

### 2.12 SHA-256 哈希追踪与损坏自愈（2026-11-15，feat/sha256-tracking）

决策：独立 `sha256sum.db` 追踪 storage 全部文件哈希（与 root.db 分库，避免元数据与完整性关注点耦合）；缓存命中对外提供前实时重算 SHA-256 比对，不一致（含无记录）即判定损坏，`invalidate_file` 删除该音质主文件与专辑副本后走重新下载 → 重写元数据 → 重登记哈希的完整链路。
取舍：校验为命中时实时重算而非后台轮询（个人规模文件量小，实时重算约 10MB < 50ms，换来零窗口的强一致）；无记录同样判定为损坏，宁可多一次重下也不提供未验证文件；升级前存量缓存无记录，首次访问自愈重下一次（本地环境已做一次性回填）。
风险边界：哈希比对只能发现「内容变化」型损坏（误删/篡改/写坏），不能发现源头就是坏数据的情况；上游返回损坏文件时表现为反复重下，由用户通过日志排查。

### 2.13 歌词嵌入与传输层（2026-11-15，feat/download-metadata）

决策：歌词以 `-metadata lyrics=` 写入音频容器（不写外部 .lrc 之外的附加嵌入方案），配合既有 title/artist/album/track/封面使文件独立携带完整音乐信息；传输层新增 4 段 Range 多线程（`multi_thread` 开关）与共享 `_RateLimiter` 均匀节流（`speed_limit_kb`，0 不限速）。
实证先行：歌词嵌入逐格式 ffprobe 实测后才写入文档（mp3/flac/m4a 三格式确认）；多线程仅在总大小 > 512KB 且上游支持 206 时启用，任何异常自动回退单流，保证弱上游兼容性。
取舍：仅同步路径（服务实际链路）接入传输层，异步/批量预留路径保持原样（避免为未使用能力扩展）；限速采用均匀节流而非严格令牌桶（长时间平均速率收敛即可，实现更简单）；`lyrics` 键在 `.ogg`/`.opus`/`.mp4` 上的行为未逐一实测，依赖「写入失败不阻断」的既有语义兜底。
附带修复：`tests/test_sha256_tracking.py` 端到端篡改写法缺陷（`seek→read` 后未回 seek，写入值恰好等于原字节导致内容未变，产品校验行为经实证正确），单独提交 `420f9a7` 留痕。

### 2.14 用户自有凭据（2026-11-15，feat/user-cookie）

决策：`/download` 接受可选 `user_cookie` 参数，前端 Cookie 输入实时持久于浏览器 `localStorage` 并随请求 body 发送；服务端仅在内存中 `parse_cookie_string` 解析并传入本次上游调用，请求结束随局部变量丢弃；`None` 时回落 `config.toml` 默认 Cookie。凭据边界：不落盘、不写日志（日志中间件本就不记录请求体）、不写任何数据库；缓存命中时不消耗凭据（零上游调用）。
安全验证：以独特哨兵值 Cookie 走完「缓存命中」「缓存缺失（打桩捕获参数）」「真实上游调用（哨兵凭据，上游 404 拒绝）」三条链路后，逐一 grep 日志文件、两个 sqlite 库（二进制安全）、storage 全部文件与 config.toml，均无残留。
取舍：凭据字段仅覆盖 `/download`（解析类接口保持默认 Cookie，按用户需求最小范围实现）；凭据完全替代而非合并默认 Cookie（用户自己的凭据语义更直观）；`type=password` 输入框防肩窥，XSS 风险由 React 重写（第十一步）时的生态进一步收敛。
边界说明：请求结束即释放的「内存清除」依赖局部变量生命周期（CPython 引用计数即时回收），不做显式 zero-fill（Python 字符串不可变，zero-fill 无法保证，属于语言层限制，实际风险面为进程内存转储场景，个人服务可接受）。

### 2.15 Flask → FastAPI 迁移（2026-11-15，feat/fastapi-migration）

决策：`main.py` 重写为 FastAPI 应用（uvicorn ASGI 托管，`access_log=False` 由统一日志单点输出请求行）；大写别名路由全部移除（前端同步改小写）；slowapi 按来源 IP 对业务五路由叠加分钟/小时双阈值限速（读 `api.*` 配置，超限 429 统一结构）；`/` 经 FileResponse 托管旧页面过渡，`/docs` 自动文档开放；依赖清单移除 Flask/Werkzeug/blinker/itsdangerous/Jinja2/MarkupSafe/click/colorama，新增 fastapi/uvicorn/slowapi/httpx 及传递依赖（33 包全固定版本）。
动机：用户要求的公开 API 与按 IP 限速能力在 Flask 生态需要额外拼装（限速/异步支持均不如 FastAPI+slowapi 成熟），且 FastAPI 的依赖注入与异常处理器使统一取参/统一响应更整洁。
取舍：路由保持同步 `def`（自动入线程池），不做 async 化——上游调用与 sqlite/ffmpeg 均为同步阻塞实现，async 化收益低而风险高；限速存储用 `memory://`（单进程语义，多进程部署需升级共享存储）；大写别名直接移除而非保留（前端同仓库可控，破坏性变更已在 `docs/api.md` 顶部与知识库标注）。
破坏性变更清单：①`/Song_V1`、`/Search`、`/Playlist`、`/Album`、`/Download` 路径不再可用；②`colorama` 等依赖移除（外部复用 `requirements.txt` 的部署需重新 `pip install -r`）；③Python 层导入 `main.app` 的集成方式由 Flask test_client 变为 `fastapi.testclient.TestClient`。

### 2.16 排行榜与防刷会话（2026-11-15，feat/ranking-antispam）

决策：新增 `session_manager.py` 独立管理防刷会话与排行榜；防刷 Cookie `nu_session` 由服务端 HMAC-SHA256 签名（密钥 32 字节随机持久化于 `storage/.ranking_secret`，0600 权限，绝不下发），载荷含随机会话 ID/签发/过期时间（默认 7 天），校验先验签再查时效，无效/过期即重签发；`cookies.db` 的 downloads 表以 `(session_id, song_id)` 为主键做交付去重——同一浏览器反复下载同一歌曲只计一次；`/rank` 按最近 30 天独立会话数出 TOP 20，并列按名称排序（SQLite BINARY 序，确定可复现）。
动机：外部脚本可无成本反复调下载接口刷排名，需要服务端可验证的会话身份；HMAC 签名 + 密钥不下发使客户端无法自造合法 Cookie。
取舍：计数取「独立会话数」而非原始次数（同一用户反复下载视为一次，直接对应用户防刷诉求）；统计窗口固定 30 天常数体现"最近"语义（可调）；排名联查 root.db 只展示有元数据的歌曲，被清理歌曲自然出榜，避免跨库级联删除的复杂度；旧页面以最小区块展示榜单，完整交互由第十一步 React 重写承接。
安全边界：HMAC 只防「伪造 Cookie」与「批量领新身份刷数」，不防同密钥服务端内部误用；密钥文件随 storage/ 被 .gitignore 排除，丢失后所有已签发 Cookie 失效（自动重签发，业务无感）。

### 2.17 智能推荐（2026-11-16，feat/smart-recommend）

决策：推荐完全基于 `root.db` 特征数据离线计算（无独立推荐库/向量服务）：候选池为缓存中已有歌曲（排除自身），打分 = 同歌手 +3、歌名 difflib 相似度（阈值 0.55）+ 相似度×2、同专辑 +1，不足 5 首按访问量降序热门兜底；`GET /recommend`（限速内）即时计算返回，每项 qualities 列出实际缓存音质版本与文件大小；前端在下载完成后自动拉取渲染推荐区块。
动机：用户下载相同/近似/同歌手歌曲时给出可直接命中的缓存内容，形成「下载 → 发现 → 再下载」闭环；推荐项天然可命中缓存（第四步的缓存键与大小数据直接复用）。
取舍：相似度用标准库 difflib 字符串比序（无分词/拼音/语义，中文近似判定为字符级，精细语义匹配属未来演进）；推荐范围限于已缓存歌曲（未缓存歌曲无法标注音质版本与大小，不符合本步展示要求）。

## 3. 历史遗留与不一致（详见 docs/knowledge-base.md）

- `entrypoint.sh` 的 `--level/--mode/--url` 与 `.env` 变量为 Docker 化时期产物，`main.py` 从未实现对应参数解析（已知问题 #2）。**已于 2026-11-15 随统一启动入口任务废除**：`entrypoint.sh` 改调 `start.py`，`.env` 删除。
- `qr_login.py` 编写时所依赖的 `QRLoginManager` 返回结构（字典）在后续重构中变更为字符串/元组，未同步更新（已知问题 #1）。
- `/download` 路由的缓存命名与下载器命名分属两个时期的设计，未统一（已知问题 #3）。
- `QualityLevel` 枚举在两个模块重复定义且未被实际引用，是历次「音质列表硬编码在路由内」演进的残留（已知问题 #9/#10）。

## 4. 记录原则

后续重要设计决策（含被否决的方案与原因）应在实施任务的同一 commit 中追加到本文档第 2 节或新增小节；仅影响单一模块的细节决策可记录在对应 `docs/modules/*.md`，并在本文档留一行指引。
