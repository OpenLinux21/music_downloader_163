# 部署运行指南

> 本文档描述项目的本地运行方式与 Docker 部署方式，内容以当前配置文件与代码实证为准。
> 修改部署相关文件时须按 `docs/doc-maintenance.md` 同步更新本文档。

## 1. 环境要求

| 项 | 要求 | 依据 |
| --- | --- | --- |
| Python | 3.12（推荐，与 Docker 镜像一致；代码未使用 3.12 专属语法，理论兼容 3.10+，但未验证） | `.venv`（3.12.16）、`Dockerfile` |
| ffmpeg | 可选。缺失时下载功能仍可用，但元数据与封面写入被跳过（运行中安装亦可被懒加载识别） | `music_downloader.py` |
| 网易云 Cookie | `cookie.txt` 需填入账号 Cookie；缺失时基础解析可用，VIP 音质大概率不可用 | `music_api.load_cookies()` |
| 网络 | 需能访问 `interface3.music.163.com`、`music.163.com`、`p3.music.126.net`；前端页面依赖 `mirrors.sustech.edu.cn` CDN | `music_api.APIConstants`、`templates/index.html` |

## 2. 本地运行

```bash
# 1. 安装依赖（建议使用虚拟环境）
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. 准备 Cookie（可选，高音质必需）
#    在项目根目录创建 cookie.txt，内容为浏览器登录网易云后的 Cookie，
#    支持 "k1=v1; k2=v2" 单行格式或每行一对的格式。

# 3. 启动服务
python main.py
# 或不激活 venv：.venv/bin/python main.py

# 4. 验证
curl http://localhost:5000/health
```

启动后监听 `0.0.0.0:5000`，浏览器访问 `http://localhost:5000` 使用 Web 界面。

启动副作用（属正常行为）：自动创建 `downloads/` 目录；生成并追加写 `music_api.log`。

命令行工具（可选）：

```bash
python qr_login.py login    # 扫码登录并写入 cookie.txt（注意：当前实现存在接口不匹配，实际不可用，见 docs/knowledge-base.md 已知问题 #1）
python qr_login.py status   # 查看 Cookie 状态
python qr_login.py logout   # 备份并清除 Cookie
python music_api.py         # 打印模块功能说明
python music_downloader.py <music_id> [quality] [--download]  # 命令行查询/下载单曲
```

获取可用 Cookie 的替代路径：在能正常登录的终端中运行 `python -c "from music_api import qr_login; print(qr_login())"`，按提示扫码后将输出写入 `cookie.txt`。

## 3. Docker 部署

### 3.1 镜像说明

- 基础镜像 `python:3.12-alpine3.23`；额外安装 `ffmpeg`（`apk add --no-cache ffmpeg`）。
- pip 使用阿里云镜像源安装 `requirements.txt`（全量固定版本）。
- 时区 `TZ=Asia/Shanghai`；暴露端口 5000；启动命令为 `entrypoint.sh`。

### 3.2 Docker Compose（推荐）

```bash
# 按需修改 docker-compose.yml 中 ports（如 "8080:5000"）
docker-compose up -d

# 查看日志
docker-compose logs -f
```

`docker-compose.yml` 通过 `env_file` 加载根目录 `.env`。

### 3.3 纯 Docker

```bash
docker build -t netease-music-api .
docker run -d -p 5000:5000 netease-music-api
```

> 注意：纯 `docker run` 方式不会挂载 `cookie.txt`；镜像构建时 `COPY . .` 会把构建上下文中的 `cookie.txt` 打进镜像层，存在凭据进入镜像的风险，生产部署应避免带真实凭据构建。

### 3.4 Cookie 挂载建议（可选做法）

容器内工作目录为 `/app`，服务从 `/app/cookie.txt` 读取 Cookie。如需替换 Cookie，可重建镜像或使用 `-v ./cookie.txt:/app/cookie.txt` 挂载覆盖（当前 `docker-compose.yml` 未配置该挂载）。

## 4. 环境变量与启动参数（重要事实）

`entrypoint.sh` 当前内容：

```sh
#!/bin/sh
python3 main.py --level "${LEVEL:-lossless}" --mode "${MODE:-api}" --url "${URL:-''}"
```

`.env` 当前内容：

```text
LEVEL=lossless
MODE=api
URL=http://127.0.0.1:5000
```

**经代码实证：`main.py` 不解析任何命令行参数（无 argparse），Flask 启动配置硬编码于 `APIConfig`，因此 `--level/--mode/--url` 三个参数与 `.env` 中的 `LEVEL/MODE/URL` 当前全部无效**，仅 `ports` 端口映射有效（见知识库已知问题 #2）。服务行为无法通过环境变量调整；调整端口只能改 `docker-compose.yml` 的端口映射或修改 `main.APIConfig` 源码。

`MODE`、`URL` 的原始设计意图不明（疑似历史双模式遗留），已登记知识库待确认事项。

## 5. 日志

| 通道 | 位置 | 格式要点 |
| --- | --- | --- |
| 控制台 | stdout | `时间 - music_api - 级别 - 消息` |
| 文件 | 项目根目录 `music_api.log` | 控制台格式 + `函数名:行号`；无轮转，持续追加 |

日志级别由 `APIConfig.log_level`（默认 `INFO`）控制；`music_api.log` 已被 `.gitignore` 覆盖。

## 6. 健康检查与部署验证

```bash
curl -s http://localhost:5000/health
# 期望：{"status":200,"success":true,...,"data":{"service":"running",...}}
# cookie_status=valid 仅表示 cookie.txt 非空，不代表登录态有效
```

## 7. 已知部署注意事项

1. `requirements.txt` 固定了 `requests==2.28.2`、`urllib3==1.26.15`、`cryptography==40.0.2` 等较旧版本，升级依赖属于变更任务，须评估兼容性并同步文档。
2. Alpine 镜像编译依赖（如 cryptography 的 Rust 工具链）由固定版本保证可用性，更换基础镜像或升级版本前必须重新验证构建。
3. 前端静态资源（jQuery/Bootstrap/APlayer）加载自 `mirrors.sustech.edu.cn` CDN，内网或离线环境需自行处理资源可达性。
4. 服务使用 Flask 开发服务器（`app.run(threaded=True)`），适合个人使用，不建议直接暴露公网高并发场景。
