# frontend.md — 前端变更技能约束

> 加载时机：修改 `templates/` 下文件前。前置要求：已完整读取 `docs/skills/!core-rules.md` 与 `docs/modules/frontend.md`。

## 1. 关键现状（变更前必须知晓）

1. 单文件应用，无构建流程：直接编辑 `templates/index.html`，改动即时生效（Flask 模板缓存随 debug 模式，重启进程可见）。
2. 三类前端缺陷须知晓（知识库已登记）：
   - 错误分支读取 `data.data.msg` 而错误响应无 `data` 字段，提示必然失效（已知问题 #8）；
   - 重复解析会叠加 APlayer 实例未销毁（`docs/modules/frontend.md` 第 6 节）；
   - 「下载」列表按钮仅填充 ID 跳转下载区，不直接请求。
3. 前端与后端的契约见 `docs/modules/frontend.md` 第 4 节调用表；音质下拉框在「解析」「下载」两处各自硬编码。
4. 静态资源全部来自 `mirrors.sustech.edu.cn` CDN。

## 2. 变更约束

1. 接口调用必须使用现行路径（含大写别名），不新造路径；响应字段消费与 `docs/api.md` 保持一致。
2. 修复 `data.data.msg` 类缺陷时应与后端错误结构约定一起评估（错误响应无 `data`，应改读 `message`，或由接口任务统一调整），修复后更新知识库问题状态。
3. 新增第三方前端库须评估内网可用性与 `docs/architecture.md` 技术栈表同步；优先复用已引入的 jQuery/Bootstrap/APlayer 能力。
4. 界面文案使用简体中文，与现有文案风格一致。

## 3. 必须同步的文档

| 变更 | 同步文档 |
| --- | --- |
| 接口调用点/参数/字段消费变化 | `docs/modules/frontend.md` 第 4 节、`docs/api.md`（消费关系） |
| 界面结构/功能模式变化 | `docs/modules/frontend.md` 第 3 节 |
| 音质选项变化 | `docs/modules/frontend.md`、`docs/api.md` 第 3 节，并检查两个下拉框是否同步 |
| CDN 依赖变化 | `docs/architecture.md` 技术栈表、`docs/deployment.md` 网络要求 |

## 4. 验证要求

1. 后端未变时：四模块导入冒烟确认服务可启动，`test_client().get('/')` 确认页面渲染 200。
2. 涉及交互逻辑的改动：静态走查调用表核对路径/参数/字段；涉及上游的真实交互验证需用户授权。
3. 禁止引入构建工具或改写为框架工程（属重构任务，需用户授权）。
