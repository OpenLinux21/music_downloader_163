"""统一日志系统自检脚本（无测试框架，直接运行）

覆盖范围：
1. GinFormatter 格式：事件行（OK/WARN/ERROR + 级别映射）、请求行（数字状态码、
   微秒耗时 3 位小数、IP、接口）
2. ANSI 着色：控制台变体带颜色、文件变体无转义码
3. get_logger：同组件复用实例、双 handler（控制台 + 轮转文件）
4. 落盘：logs/service.log 存在且无 ANSI 码
5. 集成：Flask test_client 请求后产生且仅产生一条 SERVER 请求行
6. 旧方案废除：导入 main 后不再生成 music_api.log
7. Werkzeug 自带请求日志被压制

运行方式：.venv/bin/python tests/test_logging_system.py
"""

import logging
import logging.handlers
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import logger as lg

LOG_PATH = ROOT / "logs" / "service.log"
TS = r"\d{4}/\d{2}/\d{2} - \d{2}:\d{2}:\d{2}"
US = r"\d+\.\d{3}µs"


def _record(level=logging.INFO, msg="测试消息", extra=None) -> logging.LogRecord:
    rec = logging.LogRecord(
        name="SERVER", level=level, pathname=__file__, lineno=1,
        msg=msg, args=(), exc_info=None,
    )
    for k, v in (extra or {}).items():
        setattr(rec, k, v)
    return rec


def test_formatter_lines() -> None:
    colored, plain = lg.GinFormatter(True), lg.GinFormatter(False)

    # 事件行：INFO→OK（绿）；ERROR→ERROR（红）
    line = colored.format(_record(logging.INFO))
    assert re.fullmatch(
        rf"\[SERVER\] {TS} \| \x1b\[32mOK\x1b\[0m \| - \| - \| - \| 测试消息", line
    ), f"INFO 事件行不匹配: {line!r}"
    line = colored.format(_record(logging.ERROR))
    assert "\x1b[31mERROR\x1b[0m" in line and "测试消息" in line
    line = colored.format(_record(logging.WARNING))
    assert "\x1b[33mWARN\x1b[0m" in line

    # 请求行：数字状态码按区间着色 + 微秒耗时
    line = plain.format(_record(msg="", extra={"status": "200", "elapsed": "12.345µs",
                                               "ip": "127.0.0.1", "interface": "GET /health"}))
    assert re.fullmatch(rf"\[SERVER\] {TS} \| 200 \| 12\.345µs \| 127\.0\.0\.1 \| GET /health", line), \
        f"文件请求行不匹配: {line!r}"
    line = colored.format(_record(msg="", extra={"status": "500", "elapsed": "0.001µs",
                                                 "ip": "1.2.3.4", "interface": "POST /download"}))
    assert "\x1b[31m500\x1b[0m" in line
    line = colored.format(_record(msg="", extra={"status": "404", "elapsed": "0.001µs",
                                                 "ip": "1.2.3.4", "interface": "GET /x"}))
    assert "\x1b[33m404\x1b[0m" in line
    # 耗时格式统一为微秒 3 位小数
    assert re.search(US, line)
    print("通过: GinFormatter 事件行/请求行/ANSI 着色/微秒耗时格式")


def test_get_logger() -> None:
    a, b = lg.get_logger("SERVER"), lg.get_logger("SERVER")
    assert a is b, "同组件未复用日志器实例"
    assert sum(isinstance(h, logging.StreamHandler) for h in a.handlers) >= 1
    file_handlers = [h for h in a.handlers if isinstance(h, logging.handlers.RotatingFileHandler)]
    assert len(file_handlers) == 1, "文件轮转 handler 数量异常"
    assert a.propagate is False
    print("通过: get_logger 组件复用与双 handler（控制台 + 轮转文件）")


def _file_lines() -> list:
    assert LOG_PATH.exists(), "logs/service.log 未生成"
    text = LOG_PATH.read_text(encoding="utf-8")
    assert "\x1b[" not in text, "日志文件中混入 ANSI 转义码"
    return [l for l in text.splitlines() if l.strip()]


def test_file_output() -> None:
    log = lg.get_logger("FILETEST")
    before = len(_file_lines())
    lg.log_request(log, 201, __import__("time").perf_counter(), "10.0.0.9", "POST", "/download")
    lines = _file_lines()
    assert len(lines) == before + 1
    assert re.search(rf"\[FILETEST\] {TS} \| 201 \| {US} \| 10\.0\.0\.9 \| POST /download", lines[-1])
    print("通过: 落盘轮转文件内容与格式")


def test_flask_integration() -> None:
    import main
    assert not (ROOT / "music_api.log").exists(), "旧 music_api.log 仍被生成"
    client = main.app.test_client()
    before = len([l for l in _file_lines() if l.startswith("[SERVER]")])
    resp = client.get("/health")
    assert resp.status_code == 200
    server_lines = [l for l in _file_lines() if l.startswith("[SERVER]")]
    assert len(server_lines) == before + 1, f"请求数不符: {before} -> {len(server_lines)}"
    assert re.search(rf"\[SERVER\] {TS} \| 200 \| {US} \| 127\.0\.0\.1 \| GET /health", server_lines[-1]), \
        f"请求行不匹配: {server_lines[-1]!r}"
    print("通过: Flask 请求产生单一 GIN 风格请求行，旧 music_api.log 已废除")


def test_downloader_component() -> None:
    from music_downloader import MusicDownloader
    d = MusicDownloader()
    before = len([l for l in _file_lines() if l.startswith("[DOWNLOADER]")])
    d.logger.warning("自检测试消息")
    lines = [l for l in _file_lines() if l.startswith("[DOWNLOADER]")]
    assert len(lines) == before + 1 and "WARN" in lines[-1] and "自检测试消息" in lines[-1]
    print("通过: DOWNLOADER 组件事件行")


def test_werkzeug_quiet() -> None:
    lg.quiet_http_server_logs()
    assert logging.getLogger("werkzeug").level == logging.ERROR
    print("通过: Werkzeug 自带请求日志已压制")


def main() -> None:
    test_formatter_lines()
    test_get_logger()
    test_file_output()
    test_flask_integration()
    test_downloader_component()
    test_werkzeug_quiet()
    print("\n全部自检通过 ✔")


if __name__ == "__main__":
    main()
