# 模块详解：Web 前端（frontend/，React + Vite + AntD）

> 本文档详解 `frontend/` 前端工程。修改前端前须先读取 `docs/skills/!dev.md` 与本文档；接口消费变化时同步 `docs/api.md`。
> 2026-11-16 由 jQuery 单文件页面（templates/index.html，已删除）重写为 React 工程；同日完成 Modern SaaS + Minimalist 视觉重构（见 `docs/history.md`）。

## 1. 工程结构

```text
frontend/
├── index.html            # Vite 入口 HTML（#root 挂载点）
├── vite.config.js        # 构建配置（outDir=dist；开发模式 API 代理到 127.0.0.1:5000）
├── package.json / package-lock.json   # 依赖锁定（npm ci 可复现构建）
└── src/
    ├── main.jsx          # React 挂载 + ConfigProvider（中文语言包 + 全局设计令牌）
    ├── App.jsx           # 全部页面组件（白色导航栏 + Tabs 主面板 + 排行榜卡片）
    ├── api.js            # 统一 API 封装（getJSON/postJSON/downloadMusic/downloadBatch/Cookie 存取）
    ├── styles.css        # 设计系统层（CSS 变量、布局、微交互、响应式）
    └── assets/music.svg  # 品牌 Logo（内联引用，无 CDN 外链）
```

- 技术栈：React 19 + Ant Design 5 + Vite 7；依赖经 npm 安装后本地打包，**无 CDN 外链**。
- 构建：`npm install && npm run build` → 产物 `frontend/dist/`（`node_modules/`、`dist/` 均被 .gitignore 排除）。
- 托管：FastAPI 的 `/` 返回 `dist/index.html`，`/assets` 挂载静态资源；dist 缺失时 `/` 返回 503 提示构建。
- 开发：`npm run dev` 启动 Vite（端口 5173），API 请求经 vite proxy 转发到本地后端。

### 1.1 视觉设计体系

视觉样式的唯一来源是两处，新增组件不得在 JSX 内散落内联样式（结果区布局的少量 style 除外）：

1. `main.jsx` 的 ConfigProvider theme：全局令牌（品牌红 `#c20c0c` 仅作强调色、`borderRadius: 10`、系统字体栈、三级文字色、克制阴影）+ 组件级令牌（Layout/Card/Tabs/Table/Button/Input）。
2. `styles.css`：CSS 变量（`:root` 设计令牌）、白色 sticky 导航栏、可折叠 Cookie 模块、主操作区通栏、结果区横向封面布局、歌曲列表文字层级、排行榜排名列表、≤768px 响应式与 `prefers-reduced-motion` 降级。

## 2. 页面与功能（覆盖全部服务能力）

页面结构：白色 sticky 导航栏（SVG Logo + 标题「网易云音乐工具箱」+ 副标语「搜索 · 解析 · 下载」+ 右侧「Cookie 设置」按钮，控制 Cookie 模块展开/收起）→ 可折叠 Cookie 设置条 → 主内容白卡（五个 Tabs，2px 圆角红色指示器）→ 下载排行榜卡片。

| 区块 | 调用 | 说明 |
| --- | --- | --- |
| 歌曲搜索 | `POST /search` | 关键词 → 表格（序号/歌曲/歌手/专辑），每行可「去解析」「去下载」（回填 ID 并切 Tab） |
| 单曲解析 | `POST /song`（`type=json`） | ID/链接 → 封面 + 标题/歌手横向信息区（消费 `pic` 字段）+ 中性/品牌红描边 Tag + `<audio>` 试听 + 歌词框；`url` 缺失时渲染警告 Alert；解析等待显示 Skeleton 骨架 |
| 歌单解析 | `GET /playlist` | 封面 + 名称 + 创建者 `creator` + 曲目数 + 简介（2 行省略可展开）+ 曲目表格 + 批量下载操作条（`BatchDownloadBar`） |
| 专辑解析 | `GET /album` | **横向头部：左侧 `coverImgUrl` 封面（无图显示图标占位），右侧专辑名、`artist`、发行时间（`publishTime` 毫秒时间戳格式化为 YYYY-MM-DD）+ 歌曲数、简介限行展开** + 曲目表格（消费后端 `songs` 字段）+ 批量下载操作条 |
| 音乐下载 | `POST /download`（`fetch` XHR 等价的 blob 方案见 `api.js`） | 见第 4 节 |
| 下载排行榜 | `GET /rank` | 页面加载拉取，TOP 20 简洁排名列表（TOP3 品牌红数字，无徽标装饰）+ 标题展示 `window_days` 统计窗口；空榜显示提示文案 |

链接预处理：`song?id=`、`playlist?id=`、`album?id=` 由前端正则提取 ID（与后端 `_extract_music_id` 口径一致，后端仍兜底解析）。

## 3. 个人 Cookie

- 可折叠设置模块（`CookieSetting`）：默认收起，摘要行显示「已配置 / 未配置」状态点；导航栏「Cookie 设置」按钮与摘要行均可切换展开态；
- 展开后为 `#user_cookie` 密码输入框 + 清除按钮，实时同步 `localStorage('netease_user_cookie')`（api.js 的 `getUserCookie/setUserCookie`）；
- 下载请求以 `user_cookie` 字段随 JSON body 发送；凭据不进入 document.cookie、不经 URL 传递、服务器不持久化。

## 4. 下载实现细节

`api.js` 的 `downloadMusic`：XHR（`responseType=blob`）`POST /download`（JSON body：id/quality/user_cookie）；成功后从 `X-Download-Filename`（URL 编码）解码服务端实际文件名（按真实音频格式，如 `歌名.flac`），`URL.createObjectURL` + 隐藏 `<a>` 触发浏览器保存；失败解析 JSON 错误体并经 antd message 提示。下载成功后自动 `GET /recommend?song_id=` 渲染智能推荐卡片（名称/歌手 + 缓存音质 Tag 含大小 + 「填入下载」按钮）。

`api.js` 的 `downloadBatch`（2026-11-16 新增）：同方案 XHR blob `POST /batch_download`（JSON body：tracks/quality/name/user_cookie），tracks 来自当前解析页表格数据；成功后解码 zip 文件名保存，并读取 `X-Batch-Total/Success/Failed` 计数头，经 antd message 提示「成功 X/共 Y 首」（有跳过时注明版权/Cookie 限制）。批量操作条 `BatchDownloadBar` 提供音质下拉（复用 `QUALITIES`）与按钮，等待期间以 `message.loading`（key 复用）持续提示，完成后同 key 替换为结果。

## 5. 修改注意事项

1. 接口契约变化须同步 `docs/api.md` 与本文档；响应统一结构 `{status, success, message, data}` 的消费在 `api.js` 集中处理（错误经 antd message 展示——修复了旧页面 `data.data.msg` 缺陷，知识库已知问题 #8 已随重写关闭）。
2. 新增音质选项：更新 `App.jsx` 的 `QUALITIES` 数组（与后端白名单、`docs/api.md` 第 3 节同步）。
3. 新增依赖须固定版本（`package-lock.json` 提交，`npm ci` 构建可复现）。
4. XSS 注意：上游返回的歌曲名/歌手等字符串直接渲染为 JSX 文本（React 自动转义），不得改为 `dangerouslySetInnerHTML`。
5. 视觉调整优先走 `main.jsx` 主题令牌与 `styles.css`（见 1.1 节），保持视觉来源唯一；封面缺失场景必须保留占位块（不编造图片 URL）。
6. 表格列、排行榜列表等数据展示的文案与字段渲染可调整，但不得改变 `api.js` 的请求参数与响应消费逻辑。
