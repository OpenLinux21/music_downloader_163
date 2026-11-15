# 数据库设计说明

> 本文档描述项目的数据库与存储体系：`storage/root.db`（元数据）、目录布局、缓存语义与清理策略。
> 依据 `docs/doc-maintenance.md` 的映射规则，项目引入数据库时必须建立并维护本文档；后续新增表（如哈希追踪、防刷会话）须同步更新本文档。
> 内容基于当前代码实证（`storage_manager.py`、`music_downloader.py`、`main.py`）。

## 1. 存储布局

```text
storage/                             # 运行时生成，整体被 .gitignore 排除
├── root.db                          # sqlite3 元数据库
├── sha256sum.db                     # sqlite3 哈希追踪库
├── cookies.db                       # sqlite3 防刷会话库（sessions / downloads 表）
├── .ranking_secret                  # HMAC-SHA256 签名密钥（0600，仅服务端持有）
├── <song_id>/                       # 每首歌曲一个文件夹（song_id 为网易云歌曲 ID）
│   ├── <歌曲名>.<ext>                # 各音质音频；扩展名被其他音质占用时为 <歌曲名>.<quality>.<ext>
│   ├── cover.jpg                    # 专辑封面（下载时落盘，供嵌入与展示复用）
│   └── lyric.lrc                    # 歌词原文（空歌词不落盘）
└── albums/<album_id>/<song_id>/     # 归属专辑的歌曲另存副本（音频 + cover.jpg + lyric.lrc）
```

- 命名清洗与下载器一致：非法字符 `<>:"/\|?*` 替换为 `_`、去首尾空格点、200 字符截断。
- 扩展名沿用既有判定链：上游 `type` > URL 后缀 > Content-Type > 默认 `.mp3`。
- 副本语义：专辑副本计入总大小并参与清理，但不作为缓存命中对象（`files.is_copy = 0` 才可命中）。
- 历史 `downloads/` 目录保留遗留文件，不再有新写入。

## 2. root.db 表结构（sqlite3，标准库，无 ORM）

### 2.1 songs 表（歌曲元数据与访问特征）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `song_id` | INTEGER PK | 网易云歌曲 ID |
| `name` | TEXT | 歌曲名 |
| `artists` | TEXT | 歌手（`/` 分隔多人） |
| `album_id` | INTEGER NULL | 专辑 ID（无专辑为 NULL，不产生副本） |
| `album_name` | TEXT | 专辑名 |
| `duration` | INTEGER | 时长（秒） |
| `visit_count` | INTEGER | 重复访问量：缓存命中复用时 +1（`record_access`） |
| `created_at` | REAL | 首次入库时间（Unix 秒；清理保留期起算点） |
| `last_access_at` | REAL | 最近访问/登记时间 |

写入时机：首次下载登记（`register_song`，UPSERT 保留累计 `visit_count`）。

### 2.2 files 表（音质文件登记）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `id` | INTEGER PK AUTOINCREMENT | — |
| `song_id` | INTEGER | 所属歌曲 |
| `quality` | TEXT | 音质等级（standard/exhigh/lossless/hires/sky/jyeffect/jymaster/dolby） |
| `ext` | TEXT | 实际扩展名（含点） |
| `path` | TEXT UNIQUE | 相对 `storage/` 的 POSIX 路径（目录整体迁移时记录不失效） |
| `size` | INTEGER | 文件字节数（ffmpeg 元数据写入后登记，与落盘一致） |
| `downloaded_at` | REAL | 登记时间 |
| `is_copy` | INTEGER DEFAULT 0 | 1 = `albums/` 专辑副本 |
| 约束 | — | `UNIQUE(song_id, quality, is_copy)` |

索引：`idx_files_song(song_id)`。

### 2.3 连接与并发模型

- 每次操作独立短连接（`sqlite3.connect(timeout=10)`），规避 Flask 多线程下的跨线程连接限制；写并发由 sqlite 内置锁兜底，适配个人使用规模。
- 登记顺序：`register_song` → 封面/歌词落盘（含哈希登记）→ ffmpeg 元数据写入 → `register_file` + `register_hash`（保证 size 与最终落盘一致）→ 专辑副本登记（含哈希）。

## 3. sha256sum.db（哈希追踪库，2026-11-15 引入）

### 3.1 file_hashes 表

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `path` | TEXT PK | 相对 `storage/` 的 POSIX 路径（与 files.path 同口径） |
| `sha256` | TEXT | 文件内容 SHA-256（`compute_sha256` 流式 1MB 分块计算） |
| `size` | INTEGER | 登记时文件字节数 |
| `song_id` | INTEGER NULL | 所属歌曲（音频/封面/歌词均有；清理联动删除键） |
| `quality` | TEXT NULL | 音质（仅音频文件有；封面/歌词为 NULL） |
| `registered_at` | REAL | 登记时间 |

覆盖范围：**storage 内全部文件**——音频主文件、专辑副本、cover.jpg、lyric.lrc。

### 3.2 完整性校验链路

```text
缓存命中（get_cached_file）→ verify_hash（重算 SHA-256 与记录比对）
   ├─ 一致 → 复用文件提供给用户（record_access 累加访问量）
   └─ 不一致（含无记录 / 文件缺失）→ 判定损坏：
        invalidate_file：删除该音质主文件 + 专辑副本 + files 行 + 哈希记录
        → 重新下载 → 重新完成元数据写入 → 重新登记哈希 → 再提供给用户
```

- 校验点在下载器缓存命中分支（`MusicDownloader`），路由层无感知；校验为每次命中实时重算（约 10MB 文件 < 50ms，个人规模可接受）。
- 升级自愈：本库引入前已存在的缓存文件无哈希记录，首次访问会走一次「失效重下」完成登记（`invalidate_file` 保留 songs 行与访问量）。

## 4. cookies.db（防刷会话库，2026-11-15 引入）

### 4.1 sessions 表（会话签发记录）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `session_id` | TEXT PK | 随机会话 ID（`secrets.token_urlsafe(16)`） |
| `issued_at` | REAL | 签发时间 |
| `expires_at` | REAL | 过期时间（默认签发后 7 天） |
| `ip` | TEXT | 签发时来源 IP |

### 4.2 downloads 表（真实交付记录）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `session_id` | TEXT | 会话 ID |
| `song_id` | INTEGER | 歌曲 |
| `quality` | TEXT | 本次交付的音质（去重刷新时覆盖） |
| `downloaded_at` | REAL | 最近交付时间 |
| 约束 | — | `PRIMARY KEY (session_id, song_id)`：同一会话对同一歌曲只计一次（防反复下载刷排名） |

### 4.3 防刷 Cookie 机制（HMAC-SHA256）

- 密钥：32 字节随机 hex，首次使用生成并持久化于 `storage/.ranking_secret`（0600 权限），**仅服务端持有，绝不下发/写日志/入 Git**；
- Cookie `nu_session` 值 = `{session_id}.{issued_at}.{expires_at}.{HMAC-SHA256 签名 hex}`，属性 HttpOnly + SameSite=Lax + Path=/，默认 7 天；
- 校验顺序：`hmac.compare_digest` 验签 → 检查时效；无效/过期即重新签发新会话并经 Set-Cookie 下发；
- 每个请求经中间件保证持有会话（`request.state.session_id`），`/download` 真实完成交付后 `record_download` 记账。

### 4.4 排行榜口径（`GET /rank`）

- 统计窗口：最近 30 天（`RANK_WINDOW_DAYS`，"最近"语义的可调常数）；
- 计数：每歌曲的**独立会话数**（`COUNT(*)`，受 downloads 主键去重约束）；
- 排序：计数降序，并列按歌曲名升序（SQLite BINARY 字节序，确定可复现）；TOP 20；
- 已清理歌曲（root.db 无 songs 元数据）不入榜。

## 5. 缓存语义

- 缓存键：`(song_id, quality)`（主存储，`is_copy = 0`）；命中条件为 DB 有记录 **且** 文件真实存在。
- 命中行为：直接复用文件，调用方执行 `record_access(song_id)` 累加 `visit_count`；响应组装读取 root.db（`song_with_file`），不再发起上游请求（修复原 `/download` 每次重复调用上游的问题）。
- 文件名冲突：同扩展名被另一音质占用时，后到音质追加音质段（`歌曲名.<quality>.<ext>`）；同音质重复解析幂等。

## 4. 清理策略（`StorageManager.cleanup`）

| 策略 | 触发条件 | 动作 |
| --- | --- | --- |
| 到期删除 | `songs.created_at` 距今超过保留期（默认 90 天） | 按歌曲粒度删除：`storage/<song_id>/`、`albums/*/<song_id>/` 与两表记录 |
| 超限淘汰 | 全部登记文件总大小（`SUM(files.size)`，含副本）超过 `config.toml` 的 `cache.max_size_mb` | 按 `visit_count` 升序（并列按 song_id 升序）整歌淘汰，循环直至回到上限内 |

执行时机：服务启动（导入 `main` 时创建 `StorageManager` 后）立即执行一次；守护线程 `storage-cleanup` 每 3600 秒循环执行；清理结果经 `STORAGE` 前缀输出（后续可接入统一日志器）。清理失败仅告警，不影响服务。

## 6. 清理策略（`StorageManager.cleanup`）

| 策略 | 触发条件 | 动作 |
| --- | --- | --- |
| 到期删除 | `songs.created_at` 距今超过保留期（默认 90 天） | 按歌曲粒度删除：`storage/<song_id>/`、`albums/*/<song_id>/` 与两表记录（含 `file_hashes` 哈希记录） |
| 超限淘汰 | 全部登记文件总大小（`SUM(files.size)`，含副本）超过 `config.toml` 的 `cache.max_size_mb` | 按 `visit_count` 升序（并列按 song_id 升序）整歌淘汰，循环直至回到上限内（同样联动删除哈希记录） |

执行时机：服务启动（导入 `main` 时创建 `StorageManager` 后）立即执行一次；守护线程 `storage-cleanup` 每 3600 秒循环执行；清理结果经 `STORAGE` 前缀输出（后续可接入统一日志器）。清理失败仅告警，不影响服务。

## 7. 变更记录

| 日期 | 变更 | 执行者 |
| --- | --- | --- |
| 2026-11-15 | 初版创建：root.db（songs/files 表）、存储目录布局、缓存键与命中语义、双策略清理 | AI Agent（feat/storage-cache 分支） |
| 2026-11-15 | 新增 sha256sum.db（file_hashes 表）：全量文件哈希登记、命中前实时校验、损坏失效重下链路、清理联动删除哈希 | AI Agent（feat/sha256-tracking 分支） |
| 2026-11-15 | 新增 cookies.db（sessions/downloads 表）与 .ranking_secret：HMAC-SHA256 防刷会话 Cookie、真实交付去重记账、/rank 排行榜口径 | AI Agent（feat/ranking-antispam 分支） |
