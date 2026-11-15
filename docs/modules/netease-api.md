# 模块详解：网易云 API 封装（music_api.py 与 qr_login.py）

> 本文档详解上游协议封装模块与二维码登录 CLI。修改这两个文件前须先读取 `docs/skills/!dev.md` 与本文档。
> 上游接口清单与协议事实同时登记于 `docs/knowledge-base.md` 第 4 节。代码变化时须按 `docs/doc-maintenance.md` 同步更新。

## 1. music_api.py 模块结构

```text
QualityLevel          音质枚举（8 项，当前未被业务逻辑引用）
APIConstants          常量：AES 密钥、UA/Referer、8 个上游 URL、默认配置与默认 Cookie
CryptoUtils           eapi 加密工具（MD5、AES-ECB、参数拼接）
HTTPClient            eapi POST 封装（text / 完整 Response 两种返回）
APIException          上游调用异常
NeteaseAPI            核心客户端：URL/详情/歌词/搜索/歌单/专辑/封面直链
QRLoginManager        二维码登录（生成 key、终端二维码、轮询状态）
向后兼容函数           url_v1 / name_v1 / lyric_v1 / search_music / playlist_detail
                      / album_detail / get_pic_url / qr_login（每次调用新建实例）
Cookie 工具            load_cookies / parse_cookie_string
```

## 2. 常量与默认值（APIConstants）

- `AES_KEY = b"e82ckenh8dichen8"`：eapi 固定加密密钥（16 字节）。
- `USER_AGENT`：伪装 `NeteaseMusicDesktop/2.10.2` 桌面客户端。
- `DEFAULT_CONFIG` / `DEFAULT_COOKIES`：`{"os": "pc", "appver": "", "osver": "", "deviceId": "pyncm!"}`；eapi 请求时 `config` 追加随机 `requestId`（20000000~30000000）放入 `header` 字段（JSON 字符串）。
- 8 个上游 URL 见 `docs/knowledge-base.md` 第 4 节表格，此处不重复。

## 3. eapi 加密（CryptoUtils.encrypt_params）

```text
输入：完整上游 URL + payload 字典
1. url_path = urlparse(url).path，并把 "/eapi/" 替换为 "/api/"
2. digest = md5_hex( f"nobody{url_path}use{json.dumps(payload)}md5forencrypt" )
3. 明文 params = f"{url_path}-36cd479b6b5-{json.dumps(payload)}-36cd479b6b5-{digest}"
4. AES-ECB（PKCS7 填充，密钥 AES_KEY）加密 → 十六进制字符串返回
```

调用方将该 hex 作为表单字段 `params` POST 上游。`json.dumps` 使用默认分隔符（`", "`、`": "`），这一拼接格式与上游校验强相关，修改序列化方式会直接导致解密失败。

## 4. HTTP 客户端（HTTPClient）

| 方法 | 返回 | 行为 |
| --- | --- | --- |
| `post_request` | 响应文本 | 表单 `{"params": 加密串}`；Cookie 为 DEFAULT_COOKIES 与入参合并（入参优先）；超时 30s；`raise_for_status` 后失败抛 `APIException` |
| `post_request_full` | 完整 `requests.Response` | 同上，供二维码登录读取 `Set-Cookie` 头 |

注意：非 eapi 的普通接口（详情/歌词/搜索/歌单/专辑）不走该客户端，由 `NeteaseAPI` 直接用 `requests` 发起。

## 5. NeteaseAPI 方法详解

| 方法 | 上游 | 协议 | 关键实现细节 |
| --- | --- | --- | --- |
| `get_song_url(song_id, quality, cookies)` | `SONG_URL_V1` | eapi | payload：`ids:[song_id]`、`level`、`encodeType`（dolby 为 `mp4`，其余 `flac`）、`header`；`sky` 追加 `immerseType=c51`；业务码非 200 抛异常 |
| `get_song_detail(song_id)` | `SONG_DETAIL_V3` | 普通 POST | data 为 `c=json([{"id":sid,"v":0}])`；**不带 UA/Referer 头、不带 Cookie** |
| `get_lyric(song_id, cookies)` | `LYRIC_API` | 普通 POST | data 含 `lv/lrv/kv/yv/ytv/yrv` 等版本位；带 UA/Referer 与 Cookie |
| `search_music(keywords, cookies, limit)` | `SEARCH_API` | 普通 POST | data `{'s': keywords, 'type': 1, 'limit': limit}`；`type` 硬编码 1（仅歌曲）；整理为 `{id,name,artists('A/B'),album,picUrl}` |
| `get_playlist_detail(playlist_id, cookies)` | `PLAYLIST_DETAIL_API` + `SONG_DETAIL_V3` | 普通 POST | 先取歌单元数据，再按 **每批 100 个** trackIds 补全歌曲详情 |
| `get_album_detail(album_id, cookies)` | `ALBUM_DETAIL_API/{id}` | 普通 GET | 封面用 `get_pic_url(album.pic)` 生成直链 |
| `netease_encrypt_id(id_str)` | — | 本地算法 | 魔串 `3go8&$8*3*3h0k(2)2` 逐字符异或 → MD5 → Base64 → `/`→`_`、`+`→`-` |
| `get_pic_url(pic_id, size=300)` | — | 拼接 | `https://p3.music.126.net/{加密串}/{pic_id}.jpg?param={size}y{size}`；`pic_id` 为空返回空串 |

统一行为：所有上游响应业务码非 200 时抛 `APIException(message)`；网络异常（`requests.RequestException`）与 JSON 解析异常分别包装为 `APIException`。

## 6. 二维码登录（QRLoginManager）

```text
generate_qr_key()   eapi QR_UNIKEY_API，payload {type:1, header} → 返回 unikey
create_qr_login()   用 qrcode 库在终端打印 ASCII 二维码
                    （内容 https://music.163.com/login?codekey={unikey}）
                    → 返回 unikey（Optional[str]）；未安装 qrcode 库时提示并返回 None
check_qr_login(unikey)
                    eapi QR_LOGIN_API，payload {key, type:1, header}
                    → 返回 (业务码, cookie字典) 元组
                    业务码：801 等待扫码 / 802 已扫待确认 / 803 登录成功
                    803 时从响应 Set-Cookie 头中提取 MUSIC_U
qr_login()          完整流程：create → while 循环 check（间隔 2s）
                    → 803 返回 "MUSIC_U=xxx;os=pc;appver=8.9.70;" 字符串
```

**接口现状警示**：`create_qr_login` 返回 `Optional[str]`、`check_qr_login` 返回 `Tuple[int, dict]`——`qr_login.py`（下节）按旧的字典协议调用它们，导致 CLI 登录路径实际不可用（知识库已知问题 #1）。修复时两份文档与知识库必须同步。

## 7. Cookie 工具

| 函数 | 行为 |
| --- | --- |
| `load_cookies(path="cookie.txt")` | 文件不存在或读取失败返回 `{}`；内容 strip 后交给解析器 |
| `parse_cookie_string(s)` | 自动识别 `;` 或换行分隔；按首个 `=` 切分键值；空键值对跳过；返回字典 |

`parse_cookie_string` 是模块级公开函数，可被其他模块复用做 Cookie 解析。

## 8. qr_login.py（二维码登录 CLI）

模块结构：`IMPORTANT_COOKIE_KEYS = ("MUSIC_U", "__csrf", "NMTID")` 与 `QRLoginClient` 类。

`QRLoginClient` 方法分组：

| 分组 | 方法 | 行为 |
| --- | --- | --- |
| Cookie 文件操作 | `_read_cookie` / `_write_cookie` / `_backup_cookie(suffix)` / `_clear_cookie` | 读写 `cookie.txt`（UTF-8）；备份命名 `cookie.txt.backup.YYYYmmdd_HHMMSS.txt`；写空串被拒绝 |
| 状态判定 | `_is_cookie_valid` / `_get_cookie_info` | 含任一重要键即有效；info 含文件路径、Cookie 数量、缺失键、mtime |
| 业务方法 | `check_existing_login` / `interactive_login` / `save_cookie` / `show_login_info` / `logout` | `save_cookie` 先备份再写入再校验；`logout` 备份后缀为 `logout` 后删除文件 |

CLI 命令：`login` / `status`(或 `info`) / `logout` / `help`；无参数进入交互菜单（登录/状态/登出/退出）。

**当前可用性**：`status`、`logout`、`show_login_info` 正常；`login` 因第 6 节所述接口不匹配不可用。运行本模块前不要求 `cookie.txt` 存在。

## 9. 修改注意事项

1. 修改加密/序列化细节前必须理解第 3 节约束，改动后需真实账号验证上游可用性。
2. 新增上游接口时：常量加入 `APIConstants`、封装为 `NeteaseAPI` 方法、提供向后兼容函数（现行模式）、登记 `docs/knowledge-base.md` 第 4 节、按需在 `main.py` 暴露路由。
3. `QualityLevel` 枚举与 `music_downloader.py` 中重复定义，且业务逻辑未引用（音质校验在 `main.py` 硬编码列表）；调整音质列表时按 `docs/doc-maintenance.md` 全量同步。
4. 修改 Cookie 解析逻辑时注意 `qr_login.py` 的有效性判定与备份行为依赖 `load_cookies`。
