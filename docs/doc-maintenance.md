# 文档维护机制

> 本文档定义「代码发生变化时必须同步更新哪些文档」的映射规则与工作流程，是文档体系保持与代码一致的保障。
> 根目录 `AGENTS.md` 为永久固定文件，其索引不随新增文档更新；**新增或删除文档一律登记到本文档第 4 节「文档登记处」**。

## 1. 触发同步的变化类型映射表

| 代码变化类型 | 必须检查并更新的文档 | 说明 |
| --- | --- | --- |
| 路由新增/删除/改名（含兼容别名） | `docs/api.md`、`docs/modules/service.md`、`docs/knowledge-base.md`（涉及行为事实时） | 需保留大写别名兼容模式 |
| 接口参数、响应结构、错误文案变化 | `docs/api.md`、`docs/modules/frontend.md`（前端有对应调用时）、`docs/knowledge-base.md` | 响应字段增删必须核对前端消费字段 |
| 音质等级列表变化（新增/删除音质） | `docs/api.md` 第 3 节、`docs/knowledge-base.md`（实证事实 4）、`docs/modules/service.md`、`docs/modules/downloader.md`（下载分支）、`docs/modules/frontend.md`（下拉选项） | 注意 `main.py` 两处硬编码列表、`/api/info` 列表、两个模块的 `QualityLevel` 枚举需一并对齐 |
| `music_api.py` 加密算法、上游 URL、请求参数变化 | `docs/modules/netease-api.md`、`docs/knowledge-base.md` 第 4 节（外部接口事实） | 上游协议属非公开契约，变化必须记录 |
| 下载流程、文件命名、扩展名判定、ffmpeg 参数变化 | `docs/modules/downloader.md`、`docs/data-flow.md` | 涉及缓存行为时同时更新 `docs/api.md` 第 4.7 节 |
| 前端交互、调用的后端接口、CDN 依赖变化 | `docs/modules/frontend.md`、`docs/api.md`（接口消费关系变化时） | — |
| 依赖变化（`requirements.txt`） | `docs/deployment.md`、`docs/architecture.md`（技术栈表） | 必须固定版本并说明升级理由 |
| Dockerfile / docker-compose / entrypoint / .env 变化 | `docs/deployment.md`、`docs/architecture.md`（目录结构） | 注意记录环境变量是否真正生效 |
| 运行版本（Python 版本、镜像）变化 | `docs/deployment.md`、`docs/architecture.md`、并在 `AGENTS.md` 之外的文档说明判定依据 | 根 `AGENTS.md` 版本声明属固定内容，需用户授权后人工修改 |
| 存储/数据流变化（新文件、新目录、Cookie 机制） | `docs/data-flow.md`、`docs/architecture.md` 第 6 节、`docs/knowledge-base.md` | 本项目无数据库；若引入数据库须新建数据库设计文档并更新本表 |
| 架构级变化（模块拆分/合并、入口变化、并发模型） | `docs/architecture.md`、受影响模块文档、`docs/data-flow.md`、`docs/history.md`（记录决策） | — |
| 修复知识库中登记的已知问题 | `docs/knowledge-base.md`（将该条标记已修复或移除） | 同时按问题涉及范围更新上表对应文档 |
| 新增/解决待确认事项 | `docs/knowledge-base.md` 第 6 节 | 确认后必须改状态为「已确认关闭」并写明依据 |

## 2. 更新流程要求

1. **同任务同步**：文档更新必须与代码修改在**同一次任务、同一个任务分支**中完成，不允许「先改码后补文档」。
2. **先读后改**：修改代码前读取对应模块文档（引言块指明）与 `docs/skills/` 强制文件（见 `AGENTS.md` 第 5 节加载规则）。
3. **实证更新**：文档修改必须基于修改后的真实代码行为，必要时运行本地验证（方式见本文第 3 节）；禁止凭记忆或意图描述行为。
4. **行号免引用**：文档引用代码位置一律使用「文件 + 类/函数名」，不引用行号，降低漂移成本。
5. **知识库持续维护**：新发现的实证事实、已知问题、待确认事项随任务登记；已解决的条目不删除，标注「已修复/已关闭」保留轨迹。
6. **设计决策留痕**：架构或核心逻辑的重要取舍写入 `docs/history.md`。

## 3. 标准验证方式

本项目无测试框架，按改动范围选择验证方式，结果必须如实报告（完整规则见 `docs/skills/!dev.md`）：

| 改动范围 | 标准验证方式 |
| --- | --- |
| 任何 Python 改动 | `.venv/bin/python -c "import main, music_api, music_downloader, qr_login"`（导入冒烟，确认无语法/导入错误） |
| 路由/接口改动 | 在上条基础上用 `main.app.test_client()` 对受影响路由做本地请求验证（无参数校验分支、错误分支必测） |
| 下载器改动 | 用 `python music_downloader.py <测试ID>` 验证信息获取；**下载与元数据写入需用户授权**（消耗上游与磁盘） |
| 纯文档改动 | 检查文档间引用路径有效、无内部矛盾；`git diff` 确认未触碰代码 |
| 部署配置改动 | 静态检查配置语法（如 `docker-compose config`）；构建类验证需用户授权 |

禁止在验证中发起真实上游解析/下载消耗（`/song`、`/download` 成功路径），除非用户明确要求。

## 4. 文档间引用规范

- 引用其他文档使用相对路径（如 `docs/api.md`），保证在仓库任意视图下可跳转。
- 各文档开头必须有一段「文档职责 + 同步义务」的引言块，格式与现有文档保持一致。

## 5. 文档登记处（新增/删除文档在此登记）

> 根目录 `AGENTS.md` 的索引为固定内容，不在此同步；本表是新增文档的唯一登记入口。

| 日期 | 文档 | 操作 | 说明 |
| --- | --- | --- | --- |
| 2026-11-15 | `docs/` 全部 11 份文档 + `docs/skills/` 5 份技能文件 | 新增 | 文档体系建设任务初版创建 |
| 2026-11-15 | `docs/skills/!dev.md` | 新增 | 用户于本任务执行期间放置的强制技能文件（极简工程师原则，309 行）。文件名以 `!` 开头，按 `AGENTS.md` 加载规则属任何任务前必读文件；因位于文档体系目录内，随本次任务一并纳入版本控制 |
| 2026-11-15 | `docs/skills/` 下 5 份任务型技能文件（`!core-rules.md`、`backend-code.md`、`api-design.md`、`deployment.md`、`frontend.md`） | 删除 | 用户要求精简，仅保留 `!dev.md`；验证方式矩阵并入本文第 3 节，任务前必读指引改由各模块文档引言块承担 |
| （示例行，勿删格式） | `docs/xxx.md` | 新增/删除 | 一句话说明 |

## 6. 文档体系当前清单

```text
AGENTS.md                         根目录全局规则（固定，见其文件头声明）
docs/
├── architecture.md               架构总览
├── knowledge-base.md             项目知识库
├── api.md                        HTTP API 说明
├── data-flow.md                  数据流说明
├── deployment.md                 部署运行指南
├── history.md                    开发历史与设计决策
├── doc-maintenance.md            本文档（维护机制）
├── modules/
│   ├── service.md                main.py 模块详解
│   ├── netease-api.md            music_api.py + qr_login.py 模块详解
│   ├── downloader.md             music_downloader.py 模块详解
│   └── frontend.md               前端详解
└── skills/
    └── !dev.md                   强制规则：极简工程师原则（用户放置，任何任务前必读）
```
