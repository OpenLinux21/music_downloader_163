# 模块详解：Web 前端（templates/index.html）

> 本文档详解唯一前端页面。修改 `templates/` 下文件前须先读取 `docs/skills/!dev.md` 与本文档。
> 代码变化时须按 `docs/doc-maintenance.md` 同步更新本文档与 `docs/api.md`（接口消费关系变化时）。

## 1. 概述

单文件前端应用（687 行 HTML/CSS/JS），无构建步骤、无独立 JS/CSS 文件，由 `/` 路由经 `render_template` 返回。页面标题「网易云音乐工具箱」。

## 2. 外部依赖（CDN）

均加载自 `mirrors.sustech.edu.cn` CDN，内网/离线环境不可用：

| 库 | 版本 | 用途 |
| --- | --- | --- |
| Bootstrap | 5.1.3（CSS + JS bundle） | 布局与组件（含大图 Modal） |
| jQuery | 3.7.1 | DOM 操作与 AJAX |
| APlayer | 1.10.1 | 音频播放器（含歌词滚动） |

## 3. 功能模式与界面结构

顶部下拉「功能选择」切换五种模式，互斥显示对应输入区：

| 模式值 | 界面 | 输入 |
| --- | --- | --- |
| `search` | 关键词 + 返回数量（默认 10，1~50） | 搜索关键词 |
| `parse` | 歌曲 ID/URL + 音质下拉（8 项含 dolby，默认 standard） | 单曲解析 |
| `playlist` | 歌单 ID/链接 | 歌单解析 |
| `album` | 专辑 ID/链接 | 专辑解析 |
| `download` | 音乐 ID/URL + **个人 Cookie 输入（可选，localStorage 持久）** + 音质下拉（默认 lossless）+ 进度条占位 | 音乐下载 |

结果区：单曲信息卡（歌名/歌手/专辑/音质/大小/大图按钮/下载链接/歌词框/APlayer 播放器）、搜索结果列表、歌单与专辑卡片（封面/名称/创建者或歌手/描述/曲目列表，每项带「解析」「下载」按钮）。

## 4. 与后端的接口调用（全部实际调用点）

| 交互 | 请求 | 请求体/参数 | 消费字段 |
| --- | --- | --- | --- |
| 搜索按钮 | `POST /Search`（表单） | `keyword`、`limit` | `data[].id/name/artists/album/picUrl`（`artists` 为 `/` 分隔串） |
| 解析按钮 | `POST /Song_V1`（`$.post` 表单） | `url`（ID 或链接原样传入）、`level`、`type=json` | `data.name/ar_name/al_name/level/size/lyric/tlyric/url/pic` |
| 歌单解析 | `GET /Playlist` | `id`（先正则 `playlist\?id=(\d+)` 提取） | `data.playlist.coverImgUrl/name/creator/description/trackCount/tracks[]` |
| 专辑解析 | `GET /Album` | `id`（正则 `album\?id=(\d+)`） | `data.album.coverImgUrl/name/artist/description/songs[]` |
| 下载按钮 | `POST /Download`（XHR，表单编码） | `id`（先正则 `song\?id=(\d+)` 提取）、`quality`、`user_cookie`（2026-11-15 新增：取自 `#user_cookie` 输入框，实时写入 `localStorage('netease_user_cookie')`，页面加载时回填）；`responseType=blob` | 音频二进制流 + 响应头 `X-Download-Message`、`X-Download-Filename`（解码后作为保存文件名） |

注意：`/Song_V1` 的 `url` 参数不做前端解析直接传后端（后端 `_extract_music_id` 处理）；而歌单/专辑/下载是前端先行提取 ID。列表中的「下载」按钮不直接请求接口，只把 ID 填入下载区并切换到下载模式，由用户再点「下载音乐」。

## 5. 关键前端算法

### 5.1 LRC 解析与翻译合并

- `lrctrim(lyrics)`：逐行匹配 `[mm:ss.xx]` 时间戳（毫秒），剥离标签、压缩空白，返回 `[时间戳, 行号, 文本]` 数组并按时间排序。
- `lrctran(lyric, tlyric)`：双指针按时间戳对齐原文与翻译，命中时追加 ` (翻译：xxx)`；最后重新格式化为标准 LRC 文本。
- 解析成功后歌词写入歌词框（`<br>` 换行）并作为 `lrc` 传给 APlayer（`lrcType: 1` 文本模式）。

### 5.2 链接/ID 提取

- `extractLinks`：正则取文本中首个 `https?://` 链接；
- `checkValidLink`：须含 `http` 且含 `music.163.com` 或 `163cn.tv`；
- `extractAndCheckId`：链接合法用链接（交给后端解析），否则取首个纯数字串（当前解析按钮路径实际未使用该函数，`/Song_V1` 直接传原文）。

### 5.3 下载实现细节

使用 `XMLHttpRequest`（非 jQuery）以便读取自定义响应头与 blob：`POST /Download`（`application/x-www-form-urlencoded`），成功后从 `X-Download-Filename`（URL 编码）解码文件名，用 `URL.createObjectURL` + 隐藏 `<a>` 触发浏览器保存；下载期间禁用按钮并显示进度条动画（装饰性，无真实进度）。

个人 Cookie（2026-11-15 新增）：输入框实时同步 `localStorage('netease_user_cookie')`（清空输入即移除），下载请求以 `user_cookie` 字段随 body 发送；凭据不进入任何 Cookie（document.cookie）也不经 URL 传递。

智能推荐（2026-11-15 新增）：下载完成后自动 `GET /recommend?song_id=` 拉取并渲染推荐区块（最多 5 首，每首展示名称/歌手 + 缓存音质版本与文件大小 badge，附「下载」按钮回填下载区）；无推荐时隐藏区块。

## 6. 已知缺陷与注意点

1. **错误提示分支失效**（知识库已知问题 #8）：各请求失败分支读取 `data.data.msg`，但后端错误响应（`APIResponse.error`）不含 `data` 字段，会抛 `TypeError`，用户看不到错误原因。修改错误结构或前端时必须两边同步。
2. `else` 分支（成功但 `status !== 200`）同样访问 `data.data.msg`，后端该场景下 `data` 亦缺失。
3. 每次解析都会 `new APlayer` 但不销毁旧实例，重复解析会叠加播放器实例。
4. 解析按钮把用户输入原样作为 `url` 传给 `/Song_V1`，依赖后端解析逻辑；若后端解析规则变化需回归此处。
5. 页脚作者署名与外链为历史内容，属界面文案。

## 7. 修改注意事项

1. 接口路径、参数名、响应字段必须与 `docs/api.md` 保持一致；后端接口变更时按映射表同步本文档第 4 节。
2. 新增音质选项需同时更新「解析」与「下载」两个下拉框（硬编码 option）。
3. CDN 依赖变化时同步 `docs/architecture.md` 技术栈表与 `docs/deployment.md` 网络要求。
