"""统一日志模块（高仿 GIN 框架风格）

替换旧的「logging 控制台 + music_api.log 文件」双写方案。

输出格式（固定字段顺序）：
    [组件名称] YYYY/MM/DD - hh:mm:ss | 状态码 | 处理耗时 | 来源IP | 接口 [| 消息]

- 状态码：HTTP 请求行输出数字状态码（按区间 ANSI 着色：2xx 绿 / 3xx 青 /
  4xx 黄 / 5xx 红）；普通事件输出 OK（绿）/ WARN（黄）/ ERROR（红）。
- 处理耗时：单位微秒，精确到小数点后 3 位（如 12345.678µs）；事件行为 "-"。
- 接口：请求行为 "METHOD /path"，事件行为 "-"；事件消息追加在行尾。

落盘：logs/service.log（RotatingFileHandler，单文件 5MB，保留 3 份备份），
文件内容不带 ANSI 转义；控制台输出带 ANSI 着色。
组件：SERVER（Web 请求与服务事件）、DOWNLOADER（下载与元数据写入）等，
通过 get_logger(component) 获取。
"""

from __future__ import annotations

import logging
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_DIR = "logs"
LOG_FILE = "service.log"
_MAX_BYTES = 5 * 1024 * 1024
_BACKUP_COUNT = 3

# ANSI 转义（colorama 未引入终端包装时直接使用原生转义码；Windows 10+ 终端均支持）
_RESET = "\x1b[0m"
_GREEN = "\x1b[32m"
_CYAN = "\x1b[36m"
_YELLOW = "\x1b[33m"
_RED = "\x1b[31m"


def color_status(status: str) -> str:
    """按状态语义返回带 ANSI 颜色的状态码文本"""
    if status == "OK" or status.startswith("2"):
        color = _GREEN
    elif status.startswith("3"):
        color = _CYAN
    elif status == "WARN" or status.startswith("4"):
        color = _YELLOW
    else:  # ERROR / 5xx / 未知
        color = _RED
    return f"{color}{status}{_RESET}"


class GinFormatter(logging.Formatter):
    """GIN 风格格式化器

    请求行通过 extra 传入 status/elapsed/ip/interface；普通事件由日志级别
    自动映射状态码（INFO→OK、WARNING→WARN、ERROR/CRITICAL→ERROR）。
    """

    def __init__(self, use_color: bool = True):
        super().__init__()
        self.use_color = use_color

    def _status(self, record: logging.LogRecord) -> str:
        status = getattr(record, "status", None)
        if status is None:
            if record.levelno >= logging.ERROR:
                status = "ERROR"
            elif record.levelno >= logging.WARNING:
                status = "WARN"
            else:
                status = "OK"
        return color_status(status) if self.use_color else status

    def format(self, record: logging.LogRecord) -> str:
        ts = self.formatTime(record, "%Y/%m/%d - %H:%M:%S")
        elapsed = getattr(record, "elapsed", "-")
        ip = getattr(record, "ip", "-")
        interface = getattr(record, "interface", "-")
        message = record.getMessage()

        line = f"[{record.name}] {ts} | {self._status(record)} | {elapsed} | {ip} | {interface}"
        if message:
            line = f"{line} | {message}"
        if record.exc_info and not self.use_color:
            line = f"{line}\n{self.formatException(record.exc_info)}"
        return line


def get_logger(component: str, level: str = "INFO") -> logging.Logger:
    """获取组件日志器（组件名即日志行首的 [组件名称]），重复调用复用同一实例

    level 仅首次创建时生效（如 "INFO"/"DEBUG"）。
    """
    logger = logging.getLogger(component)
    if not logger.handlers:
        logger.setLevel(logging.getLevelName(level.upper()))
        logger.propagate = False

        console = logging.StreamHandler()
        console.setFormatter(GinFormatter(use_color=True))
        logger.addHandler(console)

        Path(LOG_DIR).mkdir(exist_ok=True)
        file_handler = RotatingFileHandler(
            Path(LOG_DIR) / LOG_FILE,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
        )
        file_handler.setFormatter(GinFormatter(use_color=False))
        logger.addHandler(file_handler)
    return logger


def log_request(
    logger: logging.Logger,
    status_code: int,
    start: float,
    ip: str,
    method: str,
    path: str,
) -> None:
    """输出一条请求行（耗时自 start=perf_counter() 起算，单位微秒保留 3 位小数）"""
    elapsed_us = (time.perf_counter() - start) * 1_000_000
    logger.info(
        "",
        extra={
            "status": str(status_code),
            "elapsed": f"{elapsed_us:.3f}µs",
            "ip": ip,
            "interface": f"{method} {path}",
        },
    )


def quiet_http_server_logs() -> None:
    """压制 Flask/Werkzeug 开发服务器自带的逐请求日志行，避免与请求行重复"""
    logging.getLogger("werkzeug").setLevel(logging.ERROR)

