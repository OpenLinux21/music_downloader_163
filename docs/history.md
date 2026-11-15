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

## 3. 历史遗留与不一致（详见 docs/knowledge-base.md）

- `entrypoint.sh` 的 `--level/--mode/--url` 与 `.env` 变量为 Docker 化时期产物，`main.py` 从未实现对应参数解析（已知问题 #2）。**已于 2026-11-15 随统一启动入口任务废除**：`entrypoint.sh` 改调 `start.py`，`.env` 删除。
- `qr_login.py` 编写时所依赖的 `QRLoginManager` 返回结构（字典）在后续重构中变更为字符串/元组，未同步更新（已知问题 #1）。
- `/download` 路由的缓存命名与下载器命名分属两个时期的设计，未统一（已知问题 #3）。
- `QualityLevel` 枚举在两个模块重复定义且未被实际引用，是历次「音质列表硬编码在路由内」演进的残留（已知问题 #9/#10）。

## 4. 记录原则

后续重要设计决策（含被否决的方案与原因）应在实施任务的同一 commit 中追加到本文档第 2 节或新增小节；仅影响单一模块的细节决策可记录在对应 `docs/modules/*.md`，并在本文档留一行指引。
