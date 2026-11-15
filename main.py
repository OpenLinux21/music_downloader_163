"""网易云音乐API服务主程序（FastAPI）

提供网易云音乐相关API服务，包括：
- 健康检查、搜索、单曲解析、歌单解析、专辑解析、音乐下载等服务信息
- 基于来源 IP 的接口限速（slowapi，阈值读自 config.toml）
- 统一 JSON 响应结构 {status, success, message, data?} 与 GIN 风格请求行日志

说明（2026-11-15 Flask → FastAPI 迁移）：
- 路由改为小写单路径，原大写别名（/Song_V1 等）已移除（破坏性变更，见 docs/api.md）
- 路由函数保持同步 def（FastAPI 自动进线程池），与同步上游调用/sqlite 短连接兼容
- 静态页过渡期继续使用 templates/index.html（FileResponse 托管，无模板引擎）
"""

import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.exceptions import HTTPException as StarletteHTTPException

try:
    from music_api import (
        NeteaseAPI, APIException,
        url_v1, name_v1, lyric_v1, search_music,
        playlist_detail, album_detail,
        load_cookies, parse_cookie_string,
    )
    from music_downloader import MusicDownloader, DownloadException
    from storage_manager import StorageManager
    from session_manager import SessionManager, COOKIE_NAME, SESSION_TTL_SECONDS
    from config_loader import get_config
    from logger import get_logger, log_request
except ImportError as e:
    print(f"导入模块失败: {e}")
    print("请确保所有依赖模块存在且可用")
    sys.exit(1)


@dataclass
class APIConfig:
    """API配置类"""
    host: str = '127.0.0.1'
    port: int = 5000
    debug: bool = False
    storage_dir: str = 'storage'
    max_cache_mb: float = 8192  # 本地缓存大小上限（MB），读自 config.toml
    multi_thread: bool = False  # 是否允许多线程下载，读自 config.toml
    speed_limit_kb: float = 0  # 下载限速（KB/s），0 不限速，读自 config.toml
    rate_per_minute: int = 10  # API 限速：每 IP 每分钟次数，读自 config.toml
    rate_per_hour: int = 25  # API 限速：每 IP 每小时次数，读自 config.toml
    max_file_size: int = 500 * 1024 * 1024  # 500MB
    request_timeout: int = 30
    log_level: str = 'INFO'
    cors_origins: str = '*'


class APIResponse:
    """API响应工具类（统一 JSON 结构）"""

    @staticmethod
    def success(data: Any = None, message: str = 'success', status_code: int = 200) -> JSONResponse:
        """成功响应"""
        body: Dict[str, Any] = {'status': status_code, 'success': True, 'message': message}
        if data is not None:
            body['data'] = data
        return JSONResponse(content=body, status_code=status_code)

    @staticmethod
    def error(message: str, status_code: int = 400, error_code: str = None) -> JSONResponse:
        """错误响应"""
        body: Dict[str, Any] = {'status': status_code, 'success': False, 'message': message}
        if error_code:
            body['error_code'] = error_code
        return JSONResponse(content=body, status_code=status_code)


class MusicAPIService:
    """音乐API服务类"""

    def __init__(self, config: APIConfig):
        self.config = config
        self.logger = get_logger("SERVER", self.config.log_level)
        self.netease_api = NeteaseAPI()

        # 存储管理器：按歌曲 ID 分类存储，缓存上限读自 config.toml，启动清理守护线程
        self.storage = StorageManager(
            self.config.storage_dir,
            max_total_mb=float(self.config.max_cache_mb),
        )
        self.storage_path = self.storage.storage_dir
        self.downloader = MusicDownloader(
            storage_manager=self.storage,
            multi_thread=self.config.multi_thread,
            speed_limit_kb=self.config.speed_limit_kb,
        )
        # 防刷会话与排行榜（cookies.db + HMAC 签名 Cookie，密钥仅服务端持有）
        self.session_manager = SessionManager(self.config.storage_dir)

        self.logger.info(f"音乐API服务初始化完成，存储目录: {self.storage_path.resolve()}")
        self.storage.start_cleanup_daemon()

    def _get_cookies(self) -> Dict[str, str]:
        """从 config.toml 读取服务器默认 Cookie 并解析为 dict"""
        try:
            cookies = load_cookies()
            if not cookies:
                self.logger.warning("默认 Cookie 未配置或为空，部分接口（VIP 音质）将不可用")
            return cookies
        except Exception as e:
            self.logger.error(f"读取默认 Cookie 异常: {e}")
            return {}

    def _extract_music_id(self, id_or_url: str) -> str:
        """提取音乐ID"""
        try:
            # 处理短链接
            if '163cn.tv' in id_or_url:
                import requests
                response = requests.get(id_or_url, allow_redirects=False, timeout=10)
                id_or_url = response.headers.get('Location', id_or_url)

            # 处理网易云链接
            if 'music.163.com' in id_or_url:
                index = id_or_url.find('id=') + 3
                if index > 2:
                    return id_or_url[index:].split('&')[0]

            # 直接返回ID
            return str(id_or_url).strip()

        except Exception as e:
            self.logger.error(f"提取音乐ID失败: {e}")
            return str(id_or_url).strip()

    def _format_file_size(self, size_bytes: int) -> str:
        """格式化文件大小"""
        if size_bytes == 0:
            return "0B"

        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(size_bytes)
        unit_index = 0

        while size >= 1024.0 and unit_index < len(units) - 1:
            size /= 1024.0
            unit_index += 1

        return f"{size:.2f}{units[unit_index]}"

    def _get_quality_display_name(self, quality: str) -> str:
        """获取音质显示名称"""
        quality_names = {
            'standard': "标准音质",
            'exhigh': "极高音质",
            'lossless': "无损音质",
            'hires': "Hi-Res音质",
            'sky': "沉浸环绕声",
            'jyeffect': "高清环绕声",
            'jymaster': "超清母带",
            'dolby': "杜比全景声"
        }
        return quality_names.get(quality, f"未知音质({quality})")

    def _validate_request_params(self, required_params: Dict[str, Any]) -> Optional[Tuple[str, int]]:
        """验证请求参数（返回错误文案与状态码，无错误返回 None）"""
        for param_name, param_value in required_params.items():
            if not param_value:
                return f"参数 '{param_name}' 不能为空", 400
        return None


# 服务与应用实例（绑定地址/端口/缓存上限/下载/限速配置读自 config.toml）
_cfg = get_config()
config = APIConfig(
    host=str(_cfg["server"]["host"]),
    port=int(_cfg["server"]["port"]),
    max_cache_mb=float(_cfg["cache"]["max_size_mb"]),
    multi_thread=bool(_cfg["download"]["multi_thread"]),
    speed_limit_kb=float(_cfg["download"]["speed_limit_kb"]),
    rate_per_minute=int(_cfg["api"]["rate_limit_per_minute"]),
    rate_per_hour=int(_cfg["api"]["rate_limit_per_hour"]),
)
app = FastAPI(title="网易云音乐API服务", version="2.0.0")
api_service = MusicAPIService(config)
session_manager = api_service.session_manager  # 模块级别名：中间件与 /rank 使用

# 限速器：按来源 IP，阈值读自 config.toml（默认 10 次/分钟、25 次/小时）
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
RATE_MINUTE = f"{config.rate_per_minute}/minute"
RATE_HOUR = f"{config.rate_per_hour}/hour"

# CORS（与迁移前行为一致：全开放）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
    max_age=3600,
)


@app.middleware("http")
async def session_middleware(request: Request, call_next):
    """防刷会话：先验签再检查时效；无效/过期即重新签发并经 Set-Cookie 下发（HttpOnly）"""
    token = request.cookies.get(COOKIE_NAME)
    session_id = session_manager.resolve_token(token)
    issued_new = session_id is None
    if issued_new:
        token, session_id = session_manager.issue_token(
            request.client.host if request.client else ""
        )
    request.state.session_id = session_id
    response = await call_next(request)
    if issued_new:
        response.set_cookie(
            COOKIE_NAME, token,
            max_age=SESSION_TTL_SECONDS, httponly=True, samesite="lax", path="/",
        )
    return response


@app.middleware("http")
async def log_request_line(request: Request, call_next):
    """GIN 风格请求行：每请求一条（状态码/微秒耗时/来源 IP/接口）"""
    start = time.perf_counter()
    response = await call_next(request)
    log_request(
        api_service.logger,
        response.status_code,
        start,
        request.client.host if request.client else "-",
        request.method,
        request.url.path,
    )
    return response


async def get_request_data(request: Request) -> Dict[str, Any]:
    """统一取参：GET 取 query；POST 合并表单与 JSON（JSON 同名键覆盖表单）"""
    data: Dict[str, Any] = dict(request.query_params)
    if request.method == "POST":
        try:
            form = await request.form()
            data.update({k: v for k, v in form.items()})
        except Exception:
            pass
        try:
            body = await request.json()
            if isinstance(body, dict):
                data.update(body)
        except Exception:
            pass
    return data


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    """限速超限：统一响应结构的 429"""
    return APIResponse.error("请求过于频繁，请稍后再试", 429)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """HTTP 异常（404 路由不存在等）转统一结构"""
    if exc.status_code == 404:
        return APIResponse.error("请求的资源不存在", 404)
    return APIResponse.error(f"请求错误: {exc.detail}", exc.status_code)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """未处理异常兜底 500"""
    api_service.logger.error(f"服务器内部错误: {exc}\n{traceback.format_exc()}")
    return APIResponse.error("服务器内部错误", 500)


@app.get("/")
def index():
    """首页（过渡期托管现有单文件页面，第十一步由 React 构建产物替换）"""
    return FileResponse(Path("templates") / "index.html")


@app.get('/health')
def health_check():
    """健康检查API"""
    try:
        # 检查 Cookie 状态：读取 config.toml 中的默认 Cookie
        cookies = load_cookies()
        cookie_status = 'valid' if cookies else 'invalid'

        health_info = {
            'service': 'running',
            'timestamp': int(time.time()) if 'time' in sys.modules else None,
            'cookie_status': cookie_status,
            'cookie_count': len(cookies),
            'storage_dir': str(api_service.storage_path.resolve()),
            'version': '2.0.0'
        }

        return APIResponse.success(health_info, "API服务运行正常")

    except Exception as e:
        api_service.logger.error(f"健康检查失败: {e}")
        return APIResponse.error(f"健康检查失败: {str(e)}", 500)


@app.get('/search')
@app.post('/search')
@limiter.limit(RATE_MINUTE)
@limiter.limit(RATE_HOUR)
def search_music_api(request: Request, data: Dict[str, Any] = Depends(get_request_data)):
    """搜索音乐API"""
    try:
        keyword = data.get('keyword') or data.get('keywords') or data.get('q')
        limit = int(data.get('limit', 30))
        search_type = data.get('type', '1')  # 1-歌曲（上游硬编码）

        # 参数验证
        validation_error = api_service._validate_request_params({'keyword': keyword})
        if validation_error:
            return APIResponse.error(validation_error[0], validation_error[1])

        # 限制搜索数量
        if limit > 100:
            limit = 100

        cookies = api_service._get_cookies()
        result = search_music(keyword, cookies, limit)

        # search_music返回的是歌曲列表，需要包装成前端期望的格式
        if result:
            for song in result:
                if 'artists' in song:
                    song['artist_string'] = song['artists']

        return APIResponse.success(result, "搜索完成")

    except ValueError as e:
        return APIResponse.error(f"参数格式错误: {str(e)}")
    except APIException as e:
        api_service.logger.error(f"搜索上游调用失败: {e}")
        return APIResponse.error(f"搜索失败: {str(e)}", 500)
    except Exception as e:
        api_service.logger.error(f"搜索音乐异常: {e}\n{traceback.format_exc()}")
        return APIResponse.error(f"搜索失败: {str(e)}", 500)


@app.get('/song')
@app.post('/song')
@limiter.limit(RATE_MINUTE)
@limiter.limit(RATE_HOUR)
def get_song_info(request: Request, data: Dict[str, Any] = Depends(get_request_data)):
    """获取歌曲信息API"""
    try:
        song_ids = data.get('ids') or data.get('id')
        url = data.get('url')
        level = data.get('level', 'lossless')
        info_type = data.get('type', 'url')

        # 参数验证
        if not song_ids and not url:
            return APIResponse.error("必须提供 'ids'、'id' 或 'url' 参数")

        # 提取音乐ID
        music_id = api_service._extract_music_id(song_ids or url)

        # 验证音质参数
        valid_levels = ['standard', 'exhigh', 'lossless', 'hires', 'sky', 'jyeffect', 'jymaster', 'dolby']
        if level not in valid_levels:
            return APIResponse.error(f"无效的音质参数，支持: {', '.join(valid_levels)}")

        # 验证类型参数
        valid_types = ['url', 'name', 'lyric', 'json']
        if info_type not in valid_types:
            return APIResponse.error(f"无效的类型参数，支持: {', '.join(valid_types)}")

        cookies = api_service._get_cookies()

        if info_type == 'url':
            result = url_v1(music_id, level, cookies)
            if result and result.get('data') and len(result['data']) > 0:
                song_data = result['data'][0]
                response_data = {
                    'id': song_data.get('id'),
                    'url': song_data.get('url'),
                    'level': song_data.get('level'),
                    'quality_name': api_service._get_quality_display_name(song_data.get('level', level)),
                    'size': song_data.get('size'),
                    'size_formatted': api_service._format_file_size(song_data.get('size', 0)),
                    'type': song_data.get('type'),
                    'bitrate': song_data.get('br')
                }
                return APIResponse.success(response_data, "获取歌曲URL成功")
            else:
                return APIResponse.error("获取音乐URL失败，可能是版权限制或音质不支持", 404)

        elif info_type == 'name':
            result = name_v1(music_id)
            return APIResponse.success(result, "获取歌曲信息成功")

        elif info_type == 'lyric':
            result = lyric_v1(music_id, cookies)
            return APIResponse.success(result, "获取歌词成功")

        elif info_type == 'json':
            # 获取完整的歌曲信息（用于前端解析）
            song_info = name_v1(music_id)
            url_info = url_v1(music_id, level, cookies)
            lyric_info = lyric_v1(music_id, cookies)

            if not song_info or 'songs' not in song_info or not song_info['songs']:
                return APIResponse.error("未找到歌曲信息", 404)

            song_data = song_info['songs'][0]

            response_data = {
                'id': music_id,
                'name': song_data.get('name', ''),
                'ar_name': ', '.join(artist['name'] for artist in song_data.get('ar', [])),
                'al_name': song_data.get('al', {}).get('name', ''),
                'pic': song_data.get('al', {}).get('picUrl', ''),
                'level': level,
                'lyric': lyric_info.get('lrc', {}).get('lyric', '') if lyric_info else '',
                'tlyric': lyric_info.get('tlyric', {}).get('lyric', '') if lyric_info else ''
            }

            if url_info and url_info.get('data') and len(url_info['data']) > 0:
                url_data = url_info['data'][0]
                response_data.update({
                    'url': url_data.get('url', ''),
                    'size': api_service._format_file_size(url_data.get('size', 0)),
                    'level': url_data.get('level', level)
                })
            else:
                response_data.update({
                    'url': '',
                    'size': '获取失败'
                })

            return APIResponse.success(response_data, "获取歌曲信息成功")

    except APIException as e:
        api_service.logger.error(f"API调用失败: {e}")
        return APIResponse.error(f"API调用失败: {str(e)}", 500)
    except Exception as e:
        api_service.logger.error(f"获取歌曲信息异常: {e}\n{traceback.format_exc()}")
        return APIResponse.error(f"服务器错误: {str(e)}", 500)


@app.get('/playlist')
@app.post('/playlist')
@limiter.limit(RATE_MINUTE)
@limiter.limit(RATE_HOUR)
def get_playlist(request: Request, data: Dict[str, Any] = Depends(get_request_data)):
    """获取歌单详情API"""
    try:
        playlist_id = data.get('id')

        validation_error = api_service._validate_request_params({'playlist_id': playlist_id})
        if validation_error:
            return APIResponse.error(validation_error[0], validation_error[1])

        cookies = api_service._get_cookies()
        result = playlist_detail(playlist_id, cookies)

        response_data = {
            'status': 'success',
            'playlist': result
        }

        return APIResponse.success(response_data, "获取歌单详情成功")

    except Exception as e:
        api_service.logger.error(f"获取歌单异常: {e}\n{traceback.format_exc()}")
        return APIResponse.error(f"获取歌单失败: {str(e)}", 500)


@app.get('/album')
@app.post('/album')
@limiter.limit(RATE_MINUTE)
@limiter.limit(RATE_HOUR)
def get_album(request: Request, data: Dict[str, Any] = Depends(get_request_data)):
    """获取专辑详情API"""
    try:
        album_id = data.get('id')

        validation_error = api_service._validate_request_params({'album_id': album_id})
        if validation_error:
            return APIResponse.error(validation_error[0], validation_error[1])

        cookies = api_service._get_cookies()
        result = album_detail(album_id, cookies)

        response_data = {
            'status': 200,
            'album': result
        }

        return APIResponse.success(response_data, "获取专辑详情成功")

    except Exception as e:
        api_service.logger.error(f"获取专辑异常: {e}\n{traceback.format_exc()}")
        return APIResponse.error(f"获取专辑失败: {str(e)}", 500)


@app.get('/download')
@app.post('/download')
@limiter.limit(RATE_MINUTE)
@limiter.limit(RATE_HOUR)
def download_music_api(request: Request, data: Dict[str, Any] = Depends(get_request_data)):
    """下载音乐API

    下载链路统一委托下载器（内部一次性获取链接/详情/歌词，不再重复调用上游）；
    以 (song_id, quality) 为缓存键，命中直接复用文件并累加歌曲访问量。
    """
    try:
        music_id = data.get('id')
        quality = data.get('quality', 'lossless')
        return_format = data.get('format', 'file')  # file 或 json

        # 参数验证
        validation_error = api_service._validate_request_params({'music_id': music_id})
        if validation_error:
            return APIResponse.error(validation_error[0], validation_error[1])

        # 验证音质参数
        valid_qualities = ['standard', 'exhigh', 'lossless', 'hires', 'sky', 'jyeffect', 'jymaster', 'dolby']
        if quality not in valid_qualities:
            return APIResponse.error(f"无效的音质参数，支持: {', '.join(valid_qualities)}")

        # 验证返回格式
        if return_format not in ['file', 'json']:
            return APIResponse.error("返回格式只支持 'file' 或 'json'")

        music_id = api_service._extract_music_id(music_id)

        # 请求级用户凭据：仅在内存中解析并用于本次下载的上游调用，
        # 随请求结束丢弃——严禁落盘、写日志、写 DB（日志中间件不记录请求体）
        user_cookie = (data.get('user_cookie') or '').strip()
        request_cookies = parse_cookie_string(user_cookie) if user_cookie else None

        # 下载（内部含缓存命中复用与首次下载登记；无用户凭据时使用 config 默认 Cookie）
        try:
            download_result = api_service.downloader.download_music_file(
                music_id, quality, cookies=request_cookies
            )
        except DownloadException as e:
            api_service.logger.error(f"下载异常: {e}")
            return APIResponse.error(f"下载失败: {str(e)}", 404)

        if not download_result.success:
            api_service.logger.error(f"下载失败: {download_result.error_message}")
            return APIResponse.error(f"下载失败: {download_result.error_message}", 500)

        from urllib.parse import quote

        file_path = Path(download_result.file_path)
        info = download_result.music_info

        # 命中缓存复用 → 累加歌曲访问量；首次下载由登记建立初始记录
        if download_result.cached:
            api_service.storage.record_access(int(info.id))
            api_service.logger.info(f"缓存命中，复用文件: {file_path.name}")
        else:
            api_service.logger.info(f"下载完成: {file_path.name}")

        # 排行榜计数：仅真实完成交付计入（中间件保证每请求均有会话），同会话同歌曲去重
        api_service.session_manager.record_download(
            request.state.session_id, int(info.id), quality
        )

        filename = file_path.name

        if return_format == 'json':
            response_data = {
                'music_id': int(info.id),
                'name': info.name,
                'artist': info.artists,
                'album': info.album,
                'quality': quality,
                'quality_name': api_service._get_quality_display_name(quality),
                'file_type': file_path.suffix.lstrip('.'),
                'file_size': info.file_size,
                'file_size_formatted': api_service._format_file_size(info.file_size),
                'file_path': str(file_path.absolute()),
                'filename': filename,
                'duration': info.duration,
                'cached': download_result.cached
            }
            return APIResponse.success(response_data, "下载完成")
        else:
            # 返回文件下载
            if not file_path.exists():
                return APIResponse.error("文件不存在", 404)

            try:
                response = FileResponse(
                    file_path,
                    media_type=f"audio/{file_path.suffix.lstrip('.')}",
                    filename=filename,
                )
                response.headers['X-Download-Message'] = 'Download completed successfully'
                response.headers['X-Download-Filename'] = quote(filename, safe='')
                return response
            except Exception as e:
                api_service.logger.error(f"发送文件失败: {e}")
                return APIResponse.error(f"文件发送失败: {str(e)}", 500)

    except Exception as e:
        api_service.logger.error(f"下载音乐异常: {e}\n{traceback.format_exc()}")
        return APIResponse.error(f"下载异常: {str(e)}", 500)


@app.get('/rank')
@limiter.limit(RATE_MINUTE)
@limiter.limit(RATE_HOUR)
def download_rank(request: Request):
    """下载排行榜API（最近窗口内独立会话数 TOP 20，并列按名称排序）"""
    try:
        rank = session_manager.rank_top()
        return APIResponse.success(
            {"window_days": session_manager.rank_window_days, "size": len(rank), "rank": rank},
            "获取下载排行榜成功"
        )
    except Exception as e:
        api_service.logger.error(f"获取排行榜异常: {e}\n{traceback.format_exc()}")
        return APIResponse.error(f"获取排行榜失败: {str(e)}", 500)


@app.get('/api/info')
def api_info():
    """API信息接口"""
    try:
        info = {
            'name': '网易云音乐API服务',
            'version': '2.0.0',
            'description': '提供网易云音乐相关API服务',
            'endpoints': {
                '/health': 'GET - 健康检查',
                '/song': 'GET/POST - 获取歌曲信息',
                '/search': 'GET/POST - 搜索音乐',
                '/playlist': 'GET/POST - 获取歌单详情',
                '/album': 'GET/POST - 获取专辑详情',
                '/download': 'GET/POST - 下载音乐',
                '/rank': 'GET - 下载排行榜（TOP 20）',
                '/api/info': 'GET - API信息'
            },
            'supported_qualities': [
                'standard', 'exhigh', 'lossless',
                'hires', 'sky', 'jyeffect', 'jymaster'
            ],
            'config': {
                'storage_dir': str(api_service.storage_path.resolve()),
                'max_cache_mb': f"{config.max_cache_mb:g}MB",
                'max_file_size': f"{config.max_file_size // (1024*1024)}MB",
                'request_timeout': f"{config.request_timeout}s"
            }
        }

        return APIResponse.success(info, "API信息获取成功")

    except Exception as e:
        api_service.logger.error(f"获取API信息异常: {e}")
        return APIResponse.error(f"获取API信息失败: {str(e)}", 500)
