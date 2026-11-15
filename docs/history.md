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
影响：新增接口时应遵循同一模式（见 `docs/skills/api-design.md`）。

### 2.5 下载后处理用外部 ffmpeg

决策：元数据/封面写入依赖系统 `ffmpeg` 二进制（非 Python 库），按扩展名分支构造参数，杜比 MP4 启用 `+faststart`；写入失败不影响下载结果。
细节设计：临时输出 `*.tagging.*` 保留原扩展名便于 muxer 识别，成功后原子替换；ffmpeg 路径懒加载，支持运行中途安装。

### 2.6 前端单文件 + CDN

决策：`templates/index.html` 单文件承载全部交互（jQuery + Bootstrap + APlayer，CDN 引入），后端为其增加 `type=json` 聚合接口与 blob 下载响应头（`X-Download-Filename` 等）。
代价：前端依赖外网 CDN；错误分支存在 `data.data.msg` 缺陷（知识库已知问题 #8）。

### 2.7 Docker 镜像锁定 Python 3.12-alpine

决策：PR#133 将镜像与依赖锁定对齐（`python:3.12-alpine3.23`），随后移除 Docker 冒烟 CI（`3408787`）。
动机：修复依赖与镜像 Python 版本不匹配的构建问题；CI 移除原因提交信息未说明（不深究）。

## 3. 历史遗留与不一致（详见 docs/knowledge-base.md）

- `entrypoint.sh` 的 `--level/--mode/--url` 与 `.env` 变量为 Docker 化时期产物，`main.py` 从未实现对应参数解析（已知问题 #2）。
- `qr_login.py` 编写时所依赖的 `QRLoginManager` 返回结构（字典）在后续重构中变更为字符串/元组，未同步更新（已知问题 #1）。
- `/download` 路由的缓存命名与下载器命名分属两个时期的设计，未统一（已知问题 #3）。
- `QualityLevel` 枚举在两个模块重复定义且未被实际引用，是历次「音质列表硬编码在路由内」演进的残留（已知问题 #9/#10）。

## 4. 记录原则

后续重要设计决策（含被否决的方案与原因）应在实施任务的同一 commit 中追加到本文档第 2 节或新增小节；仅影响单一模块的细节决策可记录在对应 `docs/modules/*.md`，并在本文档留一行指引。
