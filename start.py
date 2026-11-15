"""统一启动入口：start.py

启动前依次执行四项检查，每项结果逐行输出到终端（检查项名称、通过或失败及原因）：
  1. 依赖安装（运行时关键第三方包可导入）
  2. 配置文件存在且可解析（config.toml，标准库 tomllib）
  3. 配置项合法（类型与取值范围校验）
  4. 监听端口未被占用（socket 试绑定）

全部通过后才初始化服务进程并启动 Flask 应用（main.app）。
"""

import socket
import sys
import time
import tomllib
from importlib.util import find_spec
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

CONFIG_FILE = "config.toml"

# 运行时关键依赖（模块名, 展示名），与 requirements.txt 对应，不含传递依赖
REQUIRED_MODULES = [
    ("flask", "Flask"),
    ("requests", "requests"),
    ("aiohttp", "aiohttp"),
    ("aiofiles", "aiofiles"),
    ("cryptography", "cryptography"),
    ("qrcode", "qrcode"),
]

CheckResult = Tuple[str, bool, str]


def _report(name: str, ok: bool, detail: str = "") -> None:
    """逐行输出一项检查结果（检查项名称、通过/失败及原因）"""
    if ok:
        suffix = f"（{detail}）" if detail else ""
        print(f"[启动检查] {name} ：通过{suffix}")
    else:
        suffix = f"（{detail}）" if detail else ""
        print(f"[启动检查] {name} ：失败{suffix}")


# ----------- 检查 1：依赖安装 -----------

def check_dependencies() -> List[CheckResult]:
    results: List[CheckResult] = []
    for module, display in REQUIRED_MODULES:
        ok = find_spec(module) is not None
        detail = "" if ok else "未安装，请先执行 pip install -r requirements.txt"
        results.append((f"依赖 {display}", ok, detail))
    return results


# ----------- 检查 2/3：配置文件与配置项 -----------

def check_config() -> Tuple[List[CheckResult], Optional[Dict[str, Any]]]:
    """检查配置文件存在性、可解析性与配置项合法性，返回（结果列表, 配置或 None）"""
    from config_loader import load_config

    results: List[CheckResult] = []
    if not Path(CONFIG_FILE).exists():
        results.append(("配置文件存在", False, f"未找到 {CONFIG_FILE}，请从 config.example.toml 复制生成"))
        return results, None
    results.append(("配置文件存在", True, CONFIG_FILE))

    try:
        cfg = load_config(CONFIG_FILE)
    except tomllib.TOMLDecodeError as e:
        results.append(("配置文件可解析", False, f"TOML 解析失败: {e}"))
        return results, None
    results.append(("配置文件可解析", True, f"{len(cfg)} 个配置节"))

    results.extend(check_config_values(cfg))
    return results, cfg


def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def check_config_values(cfg: Dict[str, Any]) -> List[CheckResult]:
    """逐项校验配置值的类型与取值范围（load_config 已合并默认值，缺键回落默认）"""
    server, cache = cfg.get("server", {}), cfg.get("cache", {})
    download, api = cfg.get("download", {}), cfg.get("api", {})

    rules = [
        ("配置项 server.host", server.get("host"),
         lambda v: isinstance(v, str) and v.strip() != "", "非空字符串"),
        ("配置项 server.port", server.get("port"),
         lambda v: isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 65535,
         "1-65535 的整数"),
        ("配置项 server.default_cookie", server.get("default_cookie"),
         lambda v: isinstance(v, str), "字符串（可为空）"),
        ("配置项 cache.max_size_mb", cache.get("max_size_mb"),
         lambda v: _is_num(v) and v > 0, "单位 MB，大于 0 的数值"),
        ("配置项 download.multi_thread", download.get("multi_thread"),
         lambda v: isinstance(v, bool), "布尔值"),
        ("配置项 download.speed_limit_kb", download.get("speed_limit_kb"),
         lambda v: _is_num(v) and v >= 0, "单位 KB/s，不小于 0 的数值（0 为不限速）"),
        ("配置项 api.rate_limit_per_minute", api.get("rate_limit_per_minute"),
         lambda v: isinstance(v, int) and not isinstance(v, bool) and v > 0,
         "每 IP 每分钟次数，正整数"),
        ("配置项 api.rate_limit_per_hour", api.get("rate_limit_per_hour"),
         lambda v: isinstance(v, int) and not isinstance(v, bool) and v > 0,
         "每 IP 每小时次数，正整数"),
    ]

    results: List[CheckResult] = []
    for name, value, ok_fn, expect in rules:
        ok = ok_fn(value)
        detail = f"当前值 {value!r}，要求 {expect}" if not ok else ""
        results.append((name, ok, detail))

    # 跨项语义校验：每小时阈值不得小于每分钟阈值
    per_min, per_hour = api.get("rate_limit_per_minute"), api.get("rate_limit_per_hour")
    if isinstance(per_min, int) and isinstance(per_hour, int) and per_hour < per_min:
        results.append(("配置项 api 限速语义", False,
                        f"rate_limit_per_hour({per_hour}) 小于 rate_limit_per_minute({per_min})"))
    else:
        results.append(("配置项 api 限速语义", True, f"{per_min} 次/分钟，{per_hour} 次/小时"))
    return results


# ----------- 检查 4：端口占用 -----------

def check_port(host: str, port: int) -> CheckResult:
    """socket 试绑定检测端口占用（存在微小 TOCTOU 窗口，仅作启动前预警）"""
    name = f"端口 {host}:{port} 未被占用"
    probe_host = "0.0.0.0" if host in ("", "0.0.0.0") else host
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((probe_host, port))
            return (name, True, "")
        except PermissionError:
            return (name, False, "绑定被拒绝（权限不足或受保护端口）")
        except OSError as e:
            return (name, False, f"绑定失败: {e}")


# ----------- 主流程 -----------

def run_all_checks() -> bool:
    """执行全部检查并逐行输出，返回是否全部通过"""
    print("=" * 60)
    print("网易云音乐API服务 - 启动自检")
    print("=" * 60)

    failed = 0
    for name, ok, detail in check_dependencies():
        _report(name, ok, detail)
        failed += 0 if ok else 1

    results, cfg = check_config()
    for name, ok, detail in results:
        _report(name, ok, detail)
        failed += 0 if ok else 1

    if cfg is not None and failed == 0:
        name, ok, detail = check_port(str(cfg["server"]["host"]), int(cfg["server"]["port"]))
        _report(name, ok, detail)
        failed += 0 if ok else 1
    else:
        print("[启动检查] 端口占用 ：未执行（前置检查未通过）")

    if failed:
        print(f"\n启动自检未通过（{failed} 项失败），服务未启动。")
    return failed == 0


def start_service() -> None:
    """初始化服务进程并启动 Flask 应用"""
    import main  # 通过全部检查后才导入，避免带病初始化
    from logger import quiet_http_server_logs

    quiet_http_server_logs()
    host, port = main.config.host, main.config.port

    print("\n" + "=" * 60)
    print("🚀 网易云音乐API服务启动中...")
    print(f"📡 服务地址: http://{host}:{port}")
    print(f"📁 下载目录: {main.api_service.downloads_path.absolute()}")
    print("📚 API端点: / /health /song /search /playlist /album /download /api/info")
    print("🎵 支持的音质: standard, exhigh, lossless, hires, sky, jyeffect, jymaster, dolby")
    print("=" * 60)
    print(f"⏰ 启动时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print("🌟 服务已就绪，等待请求...\n")

    try:
        main.app.run(host=host, port=port, debug=False, threaded=True)
    except KeyboardInterrupt:
        print("\n\n👋 服务已停止")
    except OSError as e:
        print(f"\n❌ 启动失败: {e}")
        sys.exit(1)


def main() -> None:
    if not run_all_checks():
        sys.exit(1)
    start_service()


if __name__ == "__main__":
    main()
