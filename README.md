# 网易云音乐解析下载服务

<div align="center">

![GitHub stars](https://img.shields.io/github/stars/OpenLinux21/music_downloader_163?style=flat-square)
![GitHub forks](https://img.shields.io/github/forks/OpenLinux21/music_downloader_163?style=flat-square)
![GitHub issues](https://img.shields.io/github/issues/OpenLinux21/music_downloader_163?style=flat-square)
![GitHub license](https://img.shields.io/github/license/OpenLinux21/music_downloader_163?style=flat-square)

**基于 FastAPI 的网易云音乐搜索、解析与下载服务**

歌曲搜索 | 单曲解析 | 歌单解析 | 专辑解析 | 音乐下载 | 排行榜 | 智能推荐

[快速开始](#快速开始) • [API 总览](#api-总览) • [项目文档](./docs/architecture.md)

</div>

---

## 功能特性

- **歌曲搜索**：关键词搜索网易云曲库，单次最多返回 100 条
- **单曲解析**：支持按音质获取播放链接、歌曲信息、歌词（原文与翻译）
- **歌单 / 专辑解析**：批量获取歌单与专辑内的全部歌曲
- **音乐下载**：下载音频文件并自动写入元数据与封面（需系统安装 ffmpeg，可选）
- **缓存复用**：已下载文件按「歌曲 ID + 音质」缓存，命中前经 SHA-256 完整性校验，超限自动清理
- **下载排行榜**：按独立访客会话统计近 30 天下载 TOP 20，HMAC 签名 Cookie 防刷
- **智能推荐**：基于缓存歌曲特征（同歌手 / 歌名近似 / 同专辑 / 热门兜底）推荐 5 首
- **接口限速**：按来源 IP 双阈值限速（默认 10 次/分钟、25 次/小时，可在配置中调整）
- **Web 界面**：React + Ant Design 前端，构建产物由服务直接托管，无 CDN 外链依赖

### 音质支持（8 项）

| 参数值 | 说明 |
| --- | --- |
| `standard` | 标准音质 |
| `exhigh` | 极高音质 |
| `lossless` | 无损音质 |
| `hires` | Hi-Res 音质 |
| `jyeffect` | 高清环绕声 |
| `sky` | 沉浸环绕声 |
| `jymaster` | 超清母带 |
| `dolby` | 杜比全景声 |

> 高音质资源需要黑胶会员账号的 Cookie；实际能否取得取决于上游对 Cookie 的判定，服务端不做会员校验。

---

## 快速开始

### 环境要求

| 项 | 要求 |
| --- | --- |
| Python | 3.12（推荐，与 Docker 镜像一致） |
| Node.js | 22（仅本地部署需构建一次前端；Docker 多阶段构建自动完成） |
| ffmpeg | 可选。缺失时下载仍可用，但跳过元数据与封面写入 |
| 网易云 Cookie | 高音质资源需要黑胶会员账号的 Cookie |

### 本地部署

```bash
# 1. 克隆项目
git clone https://github.com/OpenLinux21/music_downloader_163.git
cd music_downloader_163

# 2. 安装 Python 依赖（建议虚拟环境）
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. 准备配置（config.toml 含凭据，已被 .gitignore 排除，禁止提交到 Git）
cp config.example.toml config.toml
# 编辑 config.toml，在 [server] 的 default_cookie 填入 Cookie：
# 登录网易云音乐网页版 → F12 开发者工具 → Network → 复制任意请求的 Cookie 值
# 绑定地址默认 127.0.0.1（仅本机访问）；容器或局域网部署需改为 0.0.0.0

# 4. 构建前端（React + Vite，产物 frontend/dist 由服务托管）
cd frontend
npm install
npm run build
cd ..

# 5. 启动服务（统一入口，启动前自动执行四项自检：
#    依赖安装、配置文件可解析、配置项合法、端口占用，并逐行输出结果）
python start.py

# 6. 访问
#    Web 界面：http://127.0.0.1:5000
#    健康检查：curl http://127.0.0.1:5000/health
```

> 💡 扫码获取 Cookie 的替代方式：运行
> `python -c "from music_api import qr_login; print(qr_login())"`，
> 按提示扫码后将输出的 Cookie 粘贴到 `config.toml` 的 `default_cookie`。
> 注意：`python qr_login.py login` 子命令存在已知缺陷，暂不可用（详见 `docs/knowledge-base.md`）。

### Docker 部署

```bash
# Docker Compose（推荐；按需修改 docker-compose.yml 中的 ports 端口映射）
docker-compose up -d

# 或纯 Docker（需挂载配置文件）
docker build -t netease-music-api .
docker run -d -p 5000:5000 -v ./config.toml:/app/config.toml netease-music-api
```

- 镜像基于 `python:3.12-alpine3.23`，含 ffmpeg；前端在多阶段构建中自动完成。
- 容器内监听地址需为 `0.0.0.0`（`[server] host`）才能被端口映射访问；未挂载配置时启动脚本会自动生成默认配置并改为 `0.0.0.0`。
- 纯 `docker run` 不挂载配置时服务无 Cookie，仅基础解析可用；`.dockerignore` 已排除 `config.toml` 与 Cookie 备份文件，凭据不会进入镜像层。

---

## 配置说明

唯一配置来源为项目根目录的 `config.toml`（从 `config.example.toml` 复制而来），修改后重启生效（Cookie 除外，实时读取）：

| 配置节 | 键 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `[server]` | `host` | `127.0.0.1` | 绑定 IP；容器或局域网部署改为 `0.0.0.0` |
| `[server]` | `port` | `5000` | 监听端口 |
| `[server]` | `default_cookie` | 空 | 服务器默认网易云 Cookie（高音质必需） |
| `[cache]` | `max_size_mb` | `8192` | 本地缓存总大小上限（MB），超限自动清理 |
| `[download]` | `multi_thread` | `false` | 是否允许多线程下载 |
| `[download]` | `speed_limit_kb` | `0` | 下载限速（KB/s），`0` 不限速 |
| `[api]` | `rate_limit_per_minute` | `10` | 每 IP 每分钟请求上限 |
| `[api]` | `rate_limit_per_hour` | `25` | 每 IP 每小时请求上限 |

---

## API 总览

统一 JSON 响应结构（`/download` 默认返回音频文件流）：

```json
// 成功
{"status": 200, "success": true, "message": "描述文本", "data": {}}
// 失败
{"status": 400, "success": false, "message": "错误描述"}
```

| 端点 | 方式 | 说明 |
| --- | --- | --- |
| `/` | GET | Web 界面（React 构建产物，未构建时返回 503 提示） |
| `/health` | GET | 健康检查（不限速） |
| `/search` | GET / POST | 关键词搜索，参数 `keyword`（或 `keywords`/`q`）、`limit`（≤ 100） |
| `/song` | GET / POST | 歌曲信息，参数 `ids`/`id`/`url` 三选一、`level` 音质、`type`（`url`/`name`/`lyric`/`json`） |
| `/playlist` | GET / POST | 歌单详情，参数 `id`（纯数字 ID） |
| `/album` | GET / POST | 专辑详情，参数 `id`（纯数字 ID） |
| `/download` | GET / POST | 下载音乐，参数 `id`、`quality` 音质、`format`（`file`/`json`）、`user_cookie`（可选请求级凭据，仅内存使用，不落盘） |
| `/batch_download` | POST | 批量下载，参数 `tracks`（`[{id, name, artists}]` 数组，≤ 100）、`quality`、`name`（zip 名）、`user_cookie`；返回 zip 包与 `X-Batch-*` 计数头 |
| `/rank` | GET | 下载排行榜 TOP 20（近 30 天独立会话数） |
| `/recommend` | GET | 智能推荐 5 首，参数 `song_id` |
| `/api/info` | GET | 服务信息（不限速） |

- **限速**：除标注「不限速」外，业务接口按来源 IP 默认 10 次/分钟 + 25 次/小时，超限返回 429。
- **链接支持**：`/song` 与 `/download` 的 ID 参数支持 `music.163.com` 链接与 `163cn.tv` 短链；`/playlist` 与 `/album` 仅接受纯数字 ID。
- **完整参数表、响应示例与错误码**：见 [`docs/api.md`](./docs/api.md)。

---

## 项目结构

```text
Netease_url/
├── start.py              # 统一启动入口（启动前四项自检，通过后启动服务）
├── main.py               # FastAPI 应用与全部 HTTP 路由
├── music_api.py          # 网易云 API 封装（eapi 加密、上游客户端、二维码登录）
├── music_downloader.py   # 音乐下载器（缓存复用、ffmpeg 元数据写入、CLI 入口）
├── storage_manager.py    # 存储与缓存管理（按歌曲 ID 分类、sqlite 元数据库、清理策略）
├── session_manager.py    # 防刷会话（HMAC 签名 Cookie）与排行榜统计
├── qr_login.py           # 二维码登录 CLI 工具（Cookie 读写 / 备份）
├── config_loader.py      # 配置读取（标准库 tomllib 解析 config.toml）
├── logger.py             # 统一日志（GIN 风格，控制台 + 文件轮转）
├── config.example.toml   # 配置模板（复制为 config.toml 后填写）
├── frontend/             # React + Vite + Ant Design 前端工程
├── storage/              # 运行时生成：音频缓存与元数据库（不入库）
├── logs/                 # 运行时生成：service.log 轮转日志（不入库）
└── docs/                 # 项目文档体系（架构 / API / 部署 / 数据流等）
```

---

## 项目文档

深入细节请阅读 `docs/` 下的文档体系：

| 文档 | 内容 |
| --- | --- |
| [`docs/architecture.md`](./docs/architecture.md) | 总体架构、目录结构、技术栈、存储方式 |
| [`docs/api.md`](./docs/api.md) | 全部 HTTP 接口的参数、响应结构与错误码 |
| [`docs/deployment.md`](./docs/deployment.md) | 本地运行、Docker 部署、日志与部署验证 |
| [`docs/data-flow.md`](./docs/data-flow.md) | 请求处理链路、下载流水线、Cookie 链路 |
| [`docs/database.md`](./docs/database.md) | sqlite 存储体系设计（root.db / 缓存布局 / 清理策略） |
| [`docs/knowledge-base.md`](./docs/knowledge-base.md) | 实证事实、外部接口事实、已知问题 |

---

## 注意事项

- 必须使用黑胶会员账号的 Cookie 才能解析高音质资源；基础解析无需 Cookie。
- `config.toml` 与 Cookie 备份文件含账号凭据，已被 `.gitignore` 排除，**严禁提交到 Git 或写入日志**。
- 服务默认单进程运行（uvicorn），个人使用场景足够；公网高并发建议前置反向代理并升级限速存储。

---

## 授权说明 / License

本仓库中的源代码基于 **MIT License** 开源，任何人可按 MIT 条款使用、修改和分发本仓库代码。

⚠️ 注意：
线上体验版本（[在线体验](https://wyapi.toubiec.cn)）是上游作者另行独立开发的闭源产品，
不属于本仓库 MIT 代码的一部分，其权利声明以对应产品页面为准。

简单说：
- 本仓库代码 → MIT
- 线上体验站 → 闭源，独立作品

---

## 致谢

- 上游项目：[Suxiaoqinx/Netease_url](https://github.com/Suxiaoqinx/Netease_url)
- [Ravizhan](https://github.com/ravizhan)

---

## 反馈与交流

- 在 GitHub [Issues](https://github.com/OpenLinux21/music_downloader_163/issues) 提交反馈

欢迎 Star、Fork 和 PR！
