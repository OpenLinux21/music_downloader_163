# deployment.md — 部署配置变更技能约束

> 加载时机：修改 `Dockerfile`、`docker-compose.yml`、`.env`、`entrypoint.sh`、`requirements.txt` 或运行版本前。前置要求：已完整读取 `docs/skills/!core-rules.md` 与 `docs/deployment.md`。

## 1. 关键现状（变更前必须知晓）

1. `main.py` 不解析命令行参数：`entrypoint.sh` 的 `--level/--mode/--url` 与 `.env` 全部变量当前无效（知识库已知问题 #2）。若要让环境变量生效，属行为新增，需修改 `main.py` 并同步全部相关文档。
2. 依赖全量固定版本；`requests==2.28.2`、`urllib3==1.26.15`、`cryptography==40.0.2` 为经验证可安装组合（Alpine + Python 3.12）。升级任一包必须本地或容器内实测导入冒烟。
3. Docker 镜像 `python:3.12-alpine3.23` 与锁定依赖版本对齐（历史 PR#133 的修复成果），更换基础镜像/版本前必须完整构建验证。
4. ffmpeg 是 apt/apk 系统包，非 Python 依赖；去除会静默降级元数据写入功能。
5. `COPY . .` 会把构建上下文整体复制进镜像，构建前确认上下文中无凭据、日志、下载产物。

## 2. 变更约束

1. `.env` 仅可放置非敏感变量；任何真实凭据禁止写入 `.env` 或提交。
2. `requirements.txt` 新增依赖必须说明用途并固定版本；禁止引入与现有功能重叠的重量级框架（如改用其他 Web 框架属重构任务）。
3. 端口、时区、镜像源等可调参数修改后，必须在 `docs/deployment.md` 同步并保留「哪些环境变量实际生效」的说明。
4. 不改动 Git 跟踪状态（如把 `cookie.txt` 移出版本控制），除非用户明确授权。

## 3. 必须同步的文档

| 变更 | 同步文档 |
| --- | --- |
| Dockerfile / entrypoint / compose / .env | `docs/deployment.md`、`docs/architecture.md`（目录结构与技术栈） |
| requirements.txt | `docs/deployment.md` 第 7 节、`docs/architecture.md` 技术栈表 |
| Python 版本/镜像版本 | `docs/deployment.md`、`docs/architecture.md`；根 `AGENTS.md` 的版本声明属固定内容，仅可提请用户人工修改 |

## 4. 验证要求

1. 配置语法验证：`docker-compose config`（compose）、`sh -n entrypoint.sh`（脚本语法）。
2. 完整构建验证（`docker build`）与容器内导入冒烟需用户授权后执行；未执行不得声称已执行。
3. 本地路径变更（如依赖升级）最低验证：四模块导入冒烟 + `.venv/bin/pip check`。
