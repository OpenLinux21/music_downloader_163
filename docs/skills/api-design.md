# api-design.md — 接口开发技能约束

> 加载时机：新增、修改或删除对外 HTTP 接口前。前置要求：已完整读取 `docs/skills/!core-rules.md` 与 `docs/modules/service.md`。

## 1. 接口设计约定（保持项目既有模式）

1. **路径命名**：业务路由使用小写；必须同时注册大写开头别名以向后兼容（现行模式：`/song`+`/Song_V1`、`/search`+`/Search`、`/playlist`+`/Playlist`、`/album`+`/Album`、`/download`+`/Download`）。
2. **方法**：统一支持 `GET` 与 `POST`（取参经 `_safe_get_request_data`，JSON 优先于表单）。
3. **响应**：一律经 `APIResponse.success` / `APIResponse.error`，不得自行拼响应字典；错误响应不得包含 `data` 字段（前端现状依赖此结构，注意其 `data.data.msg` 缺陷，见知识库已知问题 #8）。
4. **参数校验**：必填校验用 `_validate_request_params`；枚举类参数（音质、类型）用白名单列表校验并返回全部合法值。
5. **ID 解析**：涉及歌曲的接口用 `_extract_music_id`（支持短链与链接）；歌单/专辑类接口当前约定由前端提取 ID，若改为后端解析须同步前端与文档。
6. **CORS**：由 `after_request` 全局处理，路由内不得重复添加。
7. **上游调用**：在 `music_api.NeteaseAPI` 中封装新方法并提供向后兼容函数后，路由层才可使用。

## 2. 变更影响检查清单

- [ ] `docs/api.md`：新增/修改接口的参数表、响应结构、错误码
- [ ] `docs/modules/service.md`：路由清单表与实现要点
- [ ] `docs/modules/frontend.md` 第 4 节：前端是否需要联动（新字段消费、调用点变化）
- [ ] `docs/knowledge-base.md`：涉及新的行为事实或已知问题时登记
- [ ] 音质相关变化：按 `docs/doc-maintenance.md`「音质等级列表变化」行全量同步
- [ ] 兼容性：旧路径别名保留；响应字段只增不删（删除属破坏性变更，须用户确认）

## 3. 验证要求

1. 四模块导入冒烟（`docs/skills/!core-rules.md` 第 6 节）。
2. 用 `main.app.test_client()` 实测：正常分支（可用无上游依赖的桩或参数校验通过路径）、缺参 400、非法枚举 400、404 分支；把实测样例（请求与响应 JSON）回填到 `docs/api.md`，与现有文档风格一致。
3. 禁止发起真实上游解析验证，除非用户明确要求。
