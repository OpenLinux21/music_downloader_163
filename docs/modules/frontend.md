# 模块详解：Web 前端（frontend/，React + Vite + AntD）

> 本文档详解 `frontend/` 前端工程。修改前端前须先读取 `docs/skills/!dev.md` 与本文档；接口消费变化时同步 `docs/api.md`。
> 2026-11-16 由 jQuery 单文件页面（templates/index.html，已删除）重写为 React 工程。

## 1. 工程结构

```text
frontend/
├── index.html            # Vite 入口 HTML（#root 挂载点）
├── vite.config.js        # 构建配置（outDir=dist；开发模式 API 代理到 127.0.0.1:5000）
├── package.json / package-lock.json   # 依赖锁定（npm ci 可复现构建）
└── src/
    ├── main.jsx          # React 挂载 + ConfigProvider（中文语言包，主色 #c20c0c）
    ├── App.jsx           # 全部页面组件（Layout + Tabs + 排行榜卡片）
    ├── api.js            # 统一 API 封装（getJSON/postJSON/downloadMusic/downloadBatch/Cookie 存取）
    └── styles.css        # 少量全局样式
```

- 技术栈：React 19 + Ant Design 5 + Vite 7；依赖经 npm 安装后本地打包，**无 CDN 外链**。
- 构建：`npm install && npm run build` → 产物 `frontend/dist/`（`node_modules/`、`dist/` 均被 .gitignore 排除）。
- 托管：FastAPI 的 `/` 返回 `dist/index.html`，`/assets` 挂载静态资源；dist 缺失时 `/` 返回 503 提示构建。
- 开发：`npm run dev` 启动 Vite（端口 5173），API 请求经 vite proxy 转发到本地后端。

## 2. 页面与功能（覆盖全部服务能力）

顶部 Tabs：歌曲搜索 / 单曲解析 / 歌单解析 / 专辑解析 / 音乐下载；页面底部固定「下载排行榜」卡片。

| 区块 | 调用 | 说明 |
| --- | --- | --- |
| 歌曲搜索 | `POST /search` | 关键词 → 表格（歌曲/歌手/专辑），每行可「去解析」「去下载」（回填 ID 并切 Tab） |
| 单曲解析 | `POST /song`（`type=json`） | ID/链接 → 信息卡（歌名/歌手/专辑/音质/大小 + `<audio>` 试听 + 歌词框）；`url` 直链可打开 |
| 歌单解析 | `GET /playlist` | 歌单名/曲目数 + 曲目表格 + 批量下载操作条（`BatchDownloadBar`） |
| 专辑解析 | `GET /album` | 专辑名/描述 + 曲目表格（**消费后端 `songs` 字段**，2026-11-16 修复原 `tracks` 字段错配导致的空表格）+ 批量下载操作条 |
| 音乐下载 | `POST /download`（`fetch` XHR 等价的 blob 方案见 `api.js`） | 见第 4 节 |
| 下载排行榜 | `GET /rank` | 页面加载拉取，TOP 20 列表（徽标名次 + 「N 人下载」）；空榜隐藏 |

链接预处理：`song?id=`、`playlist?id=`、`album?id=` 由前端正则提取 ID（与后端 `_extract_music_id` 口径一致，后端仍兜底解析）。

## 3. 个人 Cookie

- `#user_cookie` 密码输入框 + 清除按钮，实时同步 `localStorage('netease_user_cookie')`（api.js 的 `getUserCookie/setUserCookie`）；
- 下载请求以 `user_cookie` 字段随 JSON body 发送；凭据不进入 document.cookie、不经 URL 传递、服务器不持久化。

## 4. 下载实现细节

`api.js` 的 `downloadMusic`：XHR（`responseType=blob`）`POST /download`（JSON body：id/quality/user_cookie）；成功后从 `X-Download-Filename`（URL 编码）解码服务端实际文件名（按真实音频格式，如 `歌名.flac`），`URL.createObjectURL` + 隐藏 `<a>` 触发浏览器保存；失败解析 JSON 错误体并经 antd message 提示。下载成功后自动 `GET /recommend?song_id=` 渲染智能推荐卡片（名称/歌手 + 缓存音质 Tag 含大小 + 「填入下载」按钮）。

`api.js` 的 `downloadBatch`（2026-11-16 新增）：同方案 XHR blob `POST /batch_download`（JSON body：tracks/quality/name/user_cookie），tracks 来自当前解析页表格数据；成功后解码 zip 文件名保存，并读取 `X-Batch-Total/Success/Failed` 计数头，经 antd message 提示「成功 X/共 Y 首」（有跳过时注明版权/Cookie 限制）。批量操作条 `BatchDownloadBar` 提供音质下拉（复用 `QUALITIES`）与按钮，等待期间以 `message.loading`（key 复用）持续提示，完成后同 key 替换为结果。

## 5. 修改注意事项

1. 接口契约变化须同步 `docs/api.md` 与本文档；响应统一结构 `{status, success, message, data}` 的消费在 `api.js` 集中处理（错误经 antd message 展示——修复了旧页面 `data.data.msg` 缺陷，知识库已知问题 #8 已随重写关闭）。
2. 新增音质选项：更新 `App.jsx` 的 `QUALITIES` 数组（与后端白名单、`docs/api.md` 第 3 节同步）。
3. 新增依赖须固定版本（`package-lock.json` 提交，`npm ci` 构建可复现）。
4. XSS 注意：上游返回的歌曲名/歌手等字符串直接渲染为 JSX 文本（React 自动转义），不得改为 `dangerouslySetInnerHTML`。
