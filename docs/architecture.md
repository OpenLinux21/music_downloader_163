# 项目架构总览

> 本文档描述项目的总体架构、目录结构、模块依赖关系、技术栈与存储方式。
> 内容基于对当前代码的实证分析（生成时基准分支：`develop`），代码变化时须按 `docs/doc-maintenance.md` 同步更新。

## 1. 项目定位

网易云音乐解析与下载服务：基于 Flask 的 Web 应用，对外提供网页操作界面和 JSON API，内部封装网易云音乐官方客户端接口（含 eapi 加密协议），支持歌曲搜索、单曲解析、歌单解析、专辑解析与音乐文件下载（含音频元数据与封面写入）。

- 上游依赖：网易云音乐 Web/客户端接口（`interface3.music.163.com`、`music.163.com`），高音质资源需要黑胶会员 Cookie。
- 本仓库为 `Suxiaoqinx/Netease_url` 的 Fork，远程仓库为 `OpenLinux21/music_downloader_163`，源代码基于 MIT License 开源。

## 2. 目录结构

```text
Netease_url/
├── start.py                 # 统一启动入口：启动前四项自检（依赖/配置文件/配置项/端口），通过后启动服务
├── main.py                 # Web 服务层：FastAPI 应用与全部 HTTP 路由（不含启动逻辑）
├── music_api.py            # 网易云 API 封装：eapi 加密、HTTP 客户端、二维码登录、Cookie 工具
├── music_downloader.py     # 音乐下载器：同步/异步/批量下载、缓存复用、ffmpeg 元数据写入、CLI 入口
├── storage_manager.py      # 存储与缓存管理：storage/ 布局、root.db（songs/files）、sha256sum.db（哈希）、双策略清理
├── qr_login.py             # 二维码登录 CLI 工具：Cookie 读写/备份/校验（与 music_api 存在接口不匹配，见知识库）
├── config_loader.py        # 配置读取模块：tomllib 解析 config.toml、默认值合并、Cookie 读写
├── logger.py               # 统一日志模块：GIN 风格格式（控制台彩色 + logs/service.log 轮转）
├── config.example.toml     # 配置模板（提交 Git，Cookie 留空）
├── config.toml             # 本地配置（含服务器默认 Cookie，已被 .gitignore 排除，禁止提交）
├── templates/
│   └── index.html          # 唯一前端页面（jQuery + Bootstrap + APlayer，单文件应用）
├── requirements.txt        # Python 依赖（33 个包，全部固定版本；2026-11-15 移除 Flask 系，新增 FastAPI/uvicorn/slowapi）
├── Dockerfile              # python:3.12-alpine3.23 + ffmpeg
├── docker-compose.yml      # 单服务编排（可选挂载 config.toml）
├── entrypoint.sh           # 容器启动脚本：生成默认配置后调用 python3 start.py
├── README.md / 使用文档.md  # 面向使用者的说明（部分内容与代码存在出入，以本文档体系为准）
├── tests/                  # 断言式自检脚本（无测试框架，直接运行）
├── downloads/              # 遗留下载产物目录（2026-11-15 起由 storage/ 取代，无新写入，已 gitignore）
├── storage/                # 音乐存储与元数据库（运行时生成，按歌曲 ID 分类，已 gitignore）
└── logs/                   # 统一日志目录（service.log + 轮转备份，已被 .gitignore 覆盖）
```

## 3. 技术栈

| 类别 | 内容 | 依据 |
| --- | --- | --- |
| 语言 | Python 3.12（本机 `.venv` 实测 3.12.16；Docker 镜像 `python:3.12-alpine3.23`） | `.venv/lib/python3.12/`、`Dockerfile` |
| Web 框架 | FastAPI 0.163.0（ASGI，uvicorn 0.54.0 托管，`access_log` 关闭由统一日志单点输出；路由为同步 `def`，FastAPI 自动入线程池）+ slowapi 0.1.10 按来源 IP 限速 | `main.py`、`start.py` 的 `start_service()` |
| 上游 HTTP | requests 2.28.2（同步）、aiohttp 3.12.15 + aiofiles 24.1.0（异步下载） | `requirements.txt` |
| 加密 | cryptography 40.0.2（AES-ECB + PKCS7，用于 eapi 参数加密） | `music_api.py` 的 `CryptoUtils` |
| 二维码 | qrcode 8.2（终端 ASCII 二维码） | `music_api.py` 的 `QRLoginManager.create_qr_login()` |
| 音频处理 | ffmpeg（系统二进制，非 Python 包；Docker 内通过 `apk add ffmpeg` 安装） | `Dockerfile`、`music_downloader.py` |
| 前端 | jQuery 3.7.1、Bootstrap 5.1.3、APlayer 1.10.1（均加载自 `mirrors.sustech.edu.cn` CDN） | `templates/index.html` |
| 配置 | TOML，标准库 `tomllib` 解析；`config.toml` 为唯一配置来源（缺省回落内置默认值） | `config_loader.py` |
| 日志 | 标准库 `logging` + 自研 GIN 风格 formatter：控制台（ANSI 着色）+ `logs/service.log`（5MB 轮转 ×3，无着色） | `logger.py` |
| 存储 | sqlite3 标准库（`storage/root.db`：songs/files 表 + `storage/sha256sum.db`：file_hashes 哈希表，无 ORM）+ 按歌曲 ID 分类目录；`main.py` 引入时新建设计文档 `docs/database.md` | `storage_manager.py` |

依赖全部在 `requirements.txt` 中固定版本，无数据库驱动、无 ORM、无任务队列、无测试框架、无 CI 配置。

## 4. 模块依赖关系

```text
                    ┌─────────────────────────────┐
                    │  templates/index.html       │  浏览器前端
                    │  (jQuery AJAX 调用 HTTP API) │
                    └──────────────┬──────────────┘
                                   │ HTTP (GET/POST)
                                   ▼
┌──────────────────────────────────────────────────────────┐
│ start.py （统一启动入口：四项自检 → import main → 启动）     │
│                    │                                     │
│                    ▼                                     │
│ main.py  （Web 服务层：FastAPI 应用与全部路由）             │
│  路由: / /health /song /search /playlist                │
│        /album /download /api/info                        │
│  （2026-11-15 起小写单路径，大写别名已移除）                  │
└──────────┬──────────────────────────────┬────────────────┘
           │ import                       │ import
           ▼                              ▼
┌──────────────────────────┐   ┌──────────────────────────────┐
│ music_api.py             │   │ music_downloader.py          │
│  CryptoUtils (eapi 加密)  │◄──│  MusicDownloader             │
│  HTTPClient              │   │  （经 NeteaseAPI 获取链接后    │
│  NeteaseAPI              │   │    下载文件并写元数据）         │
│  QRLoginManager          │   └──────────────────────────────┘
│  load_cookies()          │
└──────────┬───────────────┘
           │ HTTPS (上游接口)
           ▼
  interface3.music.163.com / music.163.com
（另：qr_login.py 仅依赖 music_api.py，为独立 CLI，不在 Web 链路中）
```

要点：

1. `start.py` 是唯一启动入口（四项自检通过后才导入 `main`）；`main.py` 在被导入时实例化 `MusicAPIService`（内含 `NeteaseAPI` 与 `MusicDownloader` 各一份），模块级创建 FastAPI `app`。
2. `music_downloader.py` 内部再次实例化自己的 `NeteaseAPI`；2026-11-15 起 `/download` 路由已改为直接委托下载器，不再自行调用 `name_v1`/`url_v1`，重复上游调用已消除（`/song` 聚合接口仍存在多次上游调用，详见 `docs/data-flow.md`）。
3. uvicorn 单进程 ASGI 托管 FastAPI；`access_log` 关闭，GIN 风格请求行由日志中间件单点输出；限速内存存储（`memory://`，单进程语义，多进程部署需升级为共享存储）。
4. `qr_login.py` 只依赖 `music_api.py`，用于运维人员手动登录获取 Cookie，不参与 Web 服务。
5. `music_api.py` 与 `music_downloader.py` 各自定义了 `QualityLevel` 枚举（内容一致，属重复定义，修改音质时两处都要改，见 `docs/doc-maintenance.md`）。
6. 存储层 `storage_manager.py` 被服务层（缓存命中/访问计数/清理）与下载器（落盘/登记/专辑副本）共享；`MusicAPIService` 持有同一 `StorageManager` 实例注入下载器。

## 5. 并发与运行模型

- Web 层：uvicorn 托管 FastAPI（ASGI 单进程，`access_log=False`，请求行由统一日志中间件输出）；路由为同步 `def`，FastAPI 自动派发到线程池执行；无生产级进程管理。
- 限速：slowapi 按来源 IP（`get_remote_address`）对 `/search` `/song` `/playlist` `/album` `/download` 应用每分钟/每小时双阈值（读自 `config.toml`，默认 10/25），超限 429；`/health` `/api/info` `/` 不限速。
- Cookie 读取：每个请求处理过程中调用 `load_cookies()` 实时读取 `config.toml` 的 `server.default_cookie` 并解析为字典，无缓存，修改配置即时生效。
- 下载层：同步下载使用 `requests` 流式写入（先写 `.part` 临时文件，完成后原子改名）；异步/批量下载使用 `asyncio.Semaphore(3)` 限制并发（`MusicDownloader(max_concurrent=3)`）。
- 元数据写入：调用外部 `ffmpeg` 进程（`subprocess.run`，超时 120 秒），ffmpeg 路径在启动时检测一次并在每次写入前懒加载重试（支持服务运行中途安装 ffmpeg）。
- 清理：`StorageManager` 启动守护线程 `storage-cleanup`（daemon），每 3600 秒执行一次到期删除与超限淘汰；DB 操作为每操作独立短连接，适配多线程。

## 6. 存储方式

2026-11-15 起引入 sqlite3 数据库（`storage/root.db`）与按歌曲 ID 分类目录体系，设计细节见 `docs/database.md`。持久化文件总览：

| 文件/目录 | 用途 | 产生方式 |
| --- | --- | --- |
| `config.toml` | 服务配置（绑定地址/端口/缓存上限/下载开关/限速）与服务器默认 Cookie（`server.default_cookie`） | 从 `config.example.toml` 复制后人工填写，或 `qr_login.py` 登录写入；Git 不跟踪 |
| `cookie.backup.<suffix>.<时间戳>.txt` | Cookie 覆盖/清除前的备份 | `qr_login.py` 的 `_backup_cookie()` |
| `storage/root.db` | 元数据库：`songs`（元数据与访问量）/ `files`（音质文件登记） | `storage_manager.py` 初始化与登记 |
| `storage/sha256sum.db` | 哈希追踪库：`file_hashes`（storage 全量文件 SHA-256，命中前校验、损坏失效重下） | `storage_manager.py` 登记与校验 |
| `storage/<song_id>/` | 单曲资源：各音质音频 + `cover.jpg` + `lyric.lrc` | 下载器落盘（首次下载登记，缓存命中复用） |
| `storage/albums/<album_id>/<song_id>/` | 归属专辑的歌曲副本 | 下载完成 `copy2` 复制 |
| `downloads/` | 遗留下载产物（已由 storage/ 取代，无新写入） | 历史运行产生，gitignore |
| `logs/service.log` | Web 服务与下载器统一日志（GIN 风格） | `logger.py`（控制台 + 文件各一份，文件 5MB 轮转保留 3 份） |

详细数据流见 `docs/data-flow.md`；部署方式见 `docs/deployment.md`。
