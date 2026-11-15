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
├── main.py                 # Web 服务层：Flask 应用、全部 HTTP 路由、启动入口（唯一入口）
├── music_api.py            # 网易云 API 封装：eapi 加密、HTTP 客户端、二维码登录、Cookie 工具
├── music_downloader.py     # 音乐下载器：同步/异步/批量下载、ffmpeg 元数据写入、CLI 入口
├── qr_login.py             # 二维码登录 CLI 工具：Cookie 文件读写/备份/校验（与 music_api 存在接口不匹配，见知识库）
├── templates/
│   └── index.html          # 唯一前端页面（jQuery + Bootstrap + APlayer，单文件应用）
├── requirements.txt        # Python 依赖（26 个包，全部固定版本）
├── Dockerfile              # python:3.12-alpine3.23 + ffmpeg
├── docker-compose.yml      # 单服务编排（引用 .env）
├── entrypoint.sh           # 容器启动脚本（参数实际无效，详见 docs/deployment.md）
├── .env                    # 环境变量（LEVEL/MODE/URL，当前均不被程序读取）
├── cookie.txt              # 网易云 Cookie（Git 已跟踪；用户确认为无用的示例凭据）
├── README.md / 使用文档.md  # 面向使用者的说明（部分内容与代码存在出入，以本文档体系为准）
├── downloads/              # 下载产物目录（运行时生成，未被 .gitignore 覆盖）
└── music_api.log           # 服务日志（运行时生成，已被 .gitignore 覆盖）
```

## 3. 技术栈

| 类别 | 内容 | 依据 |
| --- | --- | --- |
| 语言 | Python 3.12（本机 `.venv` 实测 3.12.16；Docker 镜像 `python:3.12-alpine3.23`） | `.venv/lib/python3.12/`、`Dockerfile` |
| Web 框架 | Flask 3.0.3（开发服务器 `app.run`，多线程模式，未使用生产级 WSGI 服务器） | `main.py` 的 `start_api_server()` |
| 上游 HTTP | requests 2.28.2（同步）、aiohttp 3.12.15 + aiofiles 24.1.0（异步下载） | `requirements.txt` |
| 加密 | cryptography 40.0.2（AES-ECB + PKCS7，用于 eapi 参数加密） | `music_api.py` 的 `CryptoUtils` |
| 二维码 | qrcode 8.2（终端 ASCII 二维码） | `music_api.py` 的 `QRLoginManager.create_qr_login()` |
| 音频处理 | ffmpeg（系统二进制，非 Python 包；Docker 内通过 `apk add ffmpeg` 安装） | `Dockerfile`、`music_downloader.py` |
| 前端 | jQuery 3.7.1、Bootstrap 5.1.3、APlayer 1.10.1（均加载自 `mirrors.sustech.edu.cn` CDN） | `templates/index.html` |

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
│ main.py  （Web 服务层，唯一启动入口）                       │
│  Flask 路由: / /health /song /search /playlist           │
│             /album /download /api/info                   │
│  （每个业务路由均注册大写开头别名以向后兼容）                  │
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

1. `main.py` 是唯一对外入口，启动时实例化 `MusicAPIService`（内含 `NeteaseAPI` 与 `MusicDownloader` 各一份），模块级创建 Flask `app`。
2. `music_downloader.py` 内部再次实例化自己的 `NeteaseAPI`，因此一次 `/download` 请求会对上游产生重复调用（详见 `docs/data-flow.md`）。
3. `qr_login.py` 只依赖 `music_api.py`，用于运维人员手动登录获取 Cookie，不参与 Web 服务。
4. `music_api.py` 与 `music_downloader.py` 各自定义了 `QualityLevel` 枚举（内容一致，属重复定义，修改音质时两处都要改，见 `docs/doc-maintenance.md`）。

## 5. 并发与运行模型

- Web 层：Flask 开发服务器 `app.run(threaded=True)`，每请求一个线程；无生产级 WSGI/进程管理。
- Cookie 读取：每个请求处理过程中调用 `load_cookies()` 重新读 `cookie.txt` 文件，无缓存，修改 Cookie 文件即时生效。
- 下载层：同步下载使用 `requests` 流式写入；异步/批量下载使用 `asyncio.Semaphore(3)` 限制并发（`MusicDownloader(max_concurrent=3)`）。
- 元数据写入：调用外部 `ffmpeg` 进程（`subprocess.run`，超时 120 秒），ffmpeg 路径在启动时检测一次并在每次写入前懒加载重试（支持服务运行中途安装 ffmpeg）。

## 6. 存储方式（无数据库）

本项目**不使用任何数据库**（无 SQLite/MySQL/PostgreSQL，无 ORM，无缓存服务），全部持久化为本地文件：

| 文件/目录 | 用途 | 产生方式 |
| --- | --- | --- |
| `cookie.txt` | 网易云账号 Cookie（`k=v; k=v` 格式） | 人工填写或 `qr_login.py` 登录写入 |
| `cookie.txt.backup.*` / `cookie.txt.logout.*` | Cookie 修改前的备份 | `qr_login.py` 的 `_backup_cookie()` |
| `downloads/` | 下载的音乐文件与 ffmpeg 封面临时文件 | 服务运行时生成 |
| `music_api.log` | Web 服务日志（控制台 + 文件双写） | `main.py` 的 `_setup_logger()` |

详细数据流见 `docs/data-flow.md`；部署方式见 `docs/deployment.md`。
