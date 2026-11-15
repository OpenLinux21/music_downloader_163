# AGENTS.md — 全局协作规则（永久固定）

> 本文件是项目根目录的全局规则文件，生成并经用户确认后视为**永久固定**。
> 任何 AI Agent 不得擅自修改、覆盖、重写、格式化或自动同步本文件中的任何内容；
> 仅当用户明确要求人工修改时才允许变更。
> 项目架构演进、知识更新与专项约束调整，一律通过维护 `docs/` 下的相应文档完成。

## 1. 运行版本

- Python **3.12**。判定依据：
  - 本项目 `.venv` 虚拟环境实测为 Python 3.12.16（`.venv/lib/python3.12/`）；
  - `Dockerfile` 基于 `python:3.12-alpine3.23`；
  - `README.md` 与 `使用文档.md` 均注明推荐 Python 3.12 与 Docker 镜像一致。
- 运行入口：`python main.py`（程序不解析任何命令行参数；`entrypoint.sh` 传入的 `--level/--mode/--url` 当前不被程序读取，详见 `docs/deployment.md`）。
- 依赖安装：`pip install -r requirements.txt`（全部为固定版本）。

## 2. 全局开发约束

1. 所有新建或修改的 Markdown 文档、注释性说明、标题、章节、表格、列表必须使用规范、准确、完整的简体中文；技术专有名词、代码符号、命令、路径、API 名称可保留英文原文。
2. 文档与沟通内容必须以代码实证为准，严禁编造事实；无法确认的内容必须标记为「待确认」并登记到 `docs/knowledge-base.md`。
3. 不得为生成文档擅自修改业务代码、改变程序行为、重构项目或安装非必要依赖。
4. 凭据安全红线：禁止将任何 Cookie、密钥、凭据、环境变量值、日志文件（`music_api.log`）、下载产物（`downloads/`）加入 Git 提交。注意：`cookie.txt` 目前被 Git 跟踪，提交前必须检查暂存区。
5. 必须遵守 `docs/skills/!core-rules.md` 中的强制规则（该文件在任何任务开始前必须完整读取）。

## 3. Git 工作流程

1. 所有开发工作以 `develop` 分支为基准。任务开始时先检查当前分支、工作区状态与 `develop` 最新状态。
2. 在 `develop` 最新状态上创建独立任务分支，命名清晰表达任务目的（如 `docs/xxx`、`feat/xxx`、`fix/xxx`）。
3. 所有代码修改与文档修改都必须形成合理的 Git commit；提交前检查变更内容，禁止混入无关文件与生成产物。
4. 每项任务完成后必须运行与改动范围相匹配的测试、检查或验证，不得声称执行过实际未执行的验证。
5. 任务完成后向用户报告修改内容、涉及文件、验证结果、commit ID 与当前分支，等待用户确认；**未经用户明确同意，严禁合并回 `develop`、严禁直接在 `develop` 上开发、严禁推送远程仓库**。
6. 若 `develop` 不存在、工作区存在无法安全处理的用户修改、或分支操作可能导致数据丢失，立即停止相关危险操作并向用户说明。

## 4. docs/ 目录职责索引

| 文档 | 职责 |
| --- | --- |
| `docs/architecture.md` | 项目总体架构、目录结构、模块依赖关系、技术栈、存储方式说明 |
| `docs/knowledge-base.md` | 项目知识库：实证事实、外部接口事实、已知问题、待确认事项（持续维护） |
| `docs/api.md` | 对外 HTTP API 完整说明（路由、参数、响应结构、错误码、音质参数） |
| `docs/data-flow.md` | 数据流说明：请求处理链路、下载流水线、Cookie 链路、文件存储 |
| `docs/deployment.md` | 部署运行指南：本地运行、Docker 部署、环境变量、日志 |
| `docs/history.md` | 开发历史与重要设计决策记录 |
| `docs/doc-maintenance.md` | 文档维护机制：代码变化与必须同步更新的文档之间的映射表 |
| `docs/modules/service.md` | Web 服务层模块详解（`main.py`） |
| `docs/modules/netease-api.md` | 网易云 API 封装模块详解（`music_api.py`、`qr_login.py`） |
| `docs/modules/downloader.md` | 音乐下载器模块详解（`music_downloader.py`） |
| `docs/modules/frontend.md` | Web 前端详解（`templates/index.html`） |

> 本索引为固定内容，不随新增文档自动更新。新增文档一律登记在 `docs/doc-maintenance.md` 的「文档登记处」，不修改本文件。

## 5. docs/skills/ 技能文件加载规则

1. `docs/skills/` 下的 Markdown 文件是按开发任务加载的专项技能与约束文档。
2. **任何文件名以英文感叹号 `!` 开头的 Markdown 文件，无论当前任务是什么，都必须在开展工作前完整读取并遵守**。当前强制文件：`docs/skills/!core-rules.md`。
3. 其余技能文件根据当前任务判断加载：
   - 修改任何 `.py` 文件前：读取 `docs/skills/backend-code.md`；
   - 新增或修改对外 HTTP 接口前：读取 `docs/skills/api-design.md`；
   - 修改 `Dockerfile`、`docker-compose.yml`、`.env`、`entrypoint.sh`、`requirements.txt` 前：读取 `docs/skills/deployment.md`；
   - 修改 `templates/` 下前端文件前：读取 `docs/skills/frontend.md`。

## 6. 文档同步维护原则

当程序发生涉及架构、核心模块运行方式、算法逻辑、接口、数据存储、配置项、依赖、数据流、部署方式或重要业务规则的变化时，必须在**同一次任务中**检查并同步更新 `docs/` 下的对应文档与知识库条目，保证文档反映代码真实状态；禁止代码已实质变化而文档仍保留旧实现。具体「变化类型 → 必须更新的文档」映射表见 `docs/doc-maintenance.md`；技能文件随任务加载时，还须遵守其中列出的同步要求。
