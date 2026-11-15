# backend-code.md — 修改 Python 代码时的技能约束

> 加载时机：修改任何 `.py` 文件前。前置要求：已完整读取 `docs/skills/!core-rules.md`。

## 1. 任务前必读文档

| 改动目标 | 必读 |
| --- | --- |
| `main.py` | `docs/modules/service.md` |
| `music_api.py` | `docs/modules/netease-api.md` |
| `music_downloader.py` | `docs/modules/downloader.md` |
| `qr_login.py` | `docs/modules/netease-api.md` 第 6、8 节 |

## 2. 编码约束

1. 遵循各文件既有风格：中文 docstring、类型注解（`typing`）、dataclass 承载数据结构、模块内类分层（常量类/工具类/业务类）。
2. 异常约定：上游错误抛 `APIException`；下载域抛 `DownloadException`；路由层捕获一切异常并转 `APIResponse.error`，不得向客户端泄漏堆栈。
3. 上游调用一律经 `music_api` 封装；路由与下载器不得直接对上游 URL 发请求。
4. Cookie 读取一律走 `load_cookies()`，不得绕过文件直读其他来源。
5. 新增常量进 `APIConstants`（或对应常量区），不散落魔法值；现有魔法串（如 eapi 分隔符）严禁改动格式。
6. 日志：`main.py` 用自带 logger；库模块避免新增 `print`（既有 `print` 调试残留见知识库已知问题 #6，允许在修复任务中处理）。

## 3. 必须同步的文档（同任务、同分支完成）

按 `docs/doc-maintenance.md` 映射表执行，速查：

- 改路由/参数/响应 → `docs/api.md` + `docs/modules/service.md`
- 改加密/上游接口/Cookie → `docs/modules/netease-api.md` + `docs/knowledge-base.md` 第 3、4 节
- 改下载/命名/ffmpeg → `docs/modules/downloader.md` + `docs/data-flow.md`
- 修复知识库已知问题 → 更新该问题条目状态
- 架构或模块边界变化 → `docs/architecture.md` + `docs/history.md`

## 4. 验证要求

按 `docs/skills/!core-rules.md` 第 6 节执行；Python 改动的最低验证为四模块导入冒烟。改动路由时补充 test_client 本地请求验证，禁止以「应该没问题」代替验证。
