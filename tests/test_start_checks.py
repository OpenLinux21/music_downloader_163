"""启动脚本自检（pytest 管理）

覆盖范围：
1. check_dependencies：当前环境全部依赖可导入
2. check_config：文件缺失 / 非法 TOML / 合法模板三种分支（作用于临时目录）
3. check_config_values：非法类型与越界值逐项检出；合法配置全过
4. check_port：空闲端口通过、被占用端口检出
5. 端到端：真实运行 python start.py，轮询端口后请求 /health 并校验响应

运行方式：python -m pytest tests/ -v
注意：端到端用例会真实占用 config.toml 配置的端口约数秒；端口被占用时自动跳过。
"""

import socket
import subprocess
import sys
import time
import tomllib
import urllib.request
from pathlib import Path

import pytest

import start

ROOT = Path(__file__).resolve().parent.parent


def test_dependencies() -> None:
    results = start.check_dependencies()
    assert all(ok for _, ok, _ in results), f"存在未安装依赖: {[n for n, ok, _ in results if not ok]}"


def test_config_checks(tmp: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # 文件缺失
    monkeypatch.setattr(start, "CONFIG_FILE", str(tmp / "missing.toml"))
    results, cfg = start.check_config()
    assert cfg is None and any(not ok for _, ok, _ in results)
    assert any("配置文件存在" in n and not ok for n, ok, _ in results)

    # 非法 TOML
    bad = tmp / "bad.toml"
    bad.write_text("port = [不是合法toml\n", encoding="utf-8")
    monkeypatch.setattr(start, "CONFIG_FILE", str(bad))
    results, cfg = start.check_config()
    assert cfg is None and any("可解析" in n and not ok for n, ok, _ in results)
    try:
        tomllib.loads(bad.read_text(encoding="utf-8"))
        raise AssertionError("bad.toml 竟可解析")
    except tomllib.TOMLDecodeError:
        pass

    # 合法模板
    good = tmp / "good.toml"
    good.write_text((ROOT / "config.example.toml").read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(start, "CONFIG_FILE", str(good))
    results, cfg = start.check_config()
    assert cfg is not None and all(ok for _, ok, _ in results)


def test_value_rules(tmp: Path) -> None:
    base = (ROOT / "config.example.toml").read_text(encoding="utf-8")

    import tomllib as _t

    from config_loader import _merge_defaults

    def values_with(override: dict) -> list:
        parsed = _t.loads(base)
        for sec, vals in override.items():  # 二级深度合并，规避 TOML 重复节限制
            parsed.setdefault(sec, {}).update(vals)
        return start.check_config_values(_merge_defaults(parsed))

    bad_cases = [
        ({"server": {"port": "abc"}}, "server.port"),
        ({"server": {"port": 0}}, "server.port"),
        ({"server": {"host": ""}}, "server.host"),
        ({"cache": {"max_size_mb": 0}}, "cache.max_size_mb"),
        ({"download": {"multi_thread": 1}}, "download.multi_thread"),
        ({"download": {"speed_limit_kb": -5}}, "download.speed_limit_kb"),
        ({"api": {"rate_limit_per_minute": 0}}, "api.rate_limit_per_minute"),
        ({"api": {"rate_limit_per_hour": 5, "rate_limit_per_minute": 10}}, "api 限速语义"),
    ]
    for override, expect_name in bad_cases:
        results = values_with(override)
        hit = [r for r in results if expect_name in r[0] and not r[1]]
        assert hit, f"未检出非法配置: {override!r}"


def test_port_check() -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
    name, ok, _ = start.check_port("127.0.0.1", free_port)
    assert ok, f"空闲端口被误判占用: {name}"

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as blocker:
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("127.0.0.1", free_port))
        blocker.listen(1)
        name, ok, detail = start.check_port("127.0.0.1", free_port)
        assert not ok and "绑定失败" in detail


def test_end_to_end() -> None:
    # 若配置端口已被其他进程占用，则跳过端到端（避免误报）
    from config_loader import get_config
    cfg = get_config()
    host, port = str(cfg["server"]["host"]), int(cfg["server"]["port"])

    def port_open() -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            try:
                s.connect((host, port))
                return True
            except OSError:
                return False

    if port_open():
        pytest.skip("端到端：配置端口已被占用，可能已有服务实例运行")

    proc = subprocess.Popen(
        [sys.executable, "start.py"],
        cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        deadline = time.time() + 25
        while time.time() < deadline and not port_open():
            time.sleep(0.5)
        assert port_open(), "start.py 未能监听端口（超时）"

        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=5) as resp:
            body = resp.read().decode("utf-8")
            assert resp.status == 200 and '"success":true' in body.replace(" ", ""), body[:200]
    finally:
        proc.terminate()
        try:
            out, _ = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, _ = proc.communicate()
    # 自检输出逐行包含检查项
    assert "[启动检查]" in out and "通过" in out, "启动输出缺少逐行检查结果"
