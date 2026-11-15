"""配置读取模块（config.toml）

项目唯一配置来源，使用 Python 3.12 标准库 tomllib 解析，无第三方依赖。

- load_config(path)：严格读取。文件缺失抛 FileNotFoundError，格式错误抛
  tomllib.TOMLDecodeError，由调用方（如 start.py 启动检查）决定如何呈现。
- get_config()：服务用便捷入口。文件缺失时回落内置默认值并告警；
  格式错误不吞（直接抛出，避免带着错误配置静默运行）。
- get_default_cookie(path)：读取服务器默认 Cookie 字符串，未配置返回空串。
- save_default_cookie(cookie, path)：把 Cookie 写回 config.toml 的 [server] 节，
  供 qr_login.py 登录后保存使用；以文本替换方式保留文件其余内容与注释。

设计说明：所有读取均不做内存缓存，修改 config.toml 即时生效（与旧方案
cookie.txt 的即时读取语义保持一致，配置文件极小，解析开销可忽略）。
"""

from __future__ import annotations

import json
import re
import sys
import tomllib
from pathlib import Path
from typing import Any

CONFIG_FILE = "config.toml"

# 内置默认值：config.toml 缺失的键回落到这里
DEFAULTS: dict[str, dict[str, Any]] = {
    "server": {
        "host": "127.0.0.1",
        "port": 5000,
        "default_cookie": "",
    },
    "cache": {
        "max_size_mb": 8192,  # 单位 MB
    },
    "download": {
        "multi_thread": False,
        "speed_limit_kb": 0,  # 单位 KB/s，0 表示不限速
    },
    "api": {
        "rate_limit_per_minute": 10,
        "rate_limit_per_hour": 25,
    },
}


def _merge_defaults(cfg: dict[str, Any]) -> dict[str, Any]:
    """把用户配置覆盖到默认值之上（仅按节合并，未知节/键原样保留）"""
    merged: dict[str, Any] = {sec: dict(vals) for sec, vals in DEFAULTS.items()}
    for sec, vals in (cfg or {}).items():
        if isinstance(vals, dict) and isinstance(merged.get(sec), dict):
            merged[sec].update(vals)
        else:
            merged[sec] = vals
    return merged


def load_config(path: str | Path = CONFIG_FILE) -> dict[str, Any]:
    """严格读取配置文件并合并默认值

    Raises:
        FileNotFoundError: 文件不存在
        tomllib.TOMLDecodeError: 内容不是合法 TOML
    """
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    return _merge_defaults(cfg)


def get_config(path: str | Path = CONFIG_FILE) -> dict[str, Any]:
    """服务用便捷读取：文件缺失回落默认值；解析错误直接抛出"""
    try:
        return load_config(path)
    except FileNotFoundError:
        print(
            f"警告: 未找到配置文件 {path}，全部使用内置默认值"
            f"（可从 config.example.toml 复制生成）",
            file=sys.stderr,
        )
        return _merge_defaults({})
    except tomllib.TOMLDecodeError as e:
        print(f"错误: 配置文件 {path} 解析失败: {e}", file=sys.stderr)
        raise


def get(section: str, key: str, default: Any = None, path: str | Path = CONFIG_FILE) -> Any:
    """读取单个配置项（优先取配置文件值，缺失回落默认值，再缺失用 default）"""
    cfg = get_config(path)
    return cfg.get(section, {}).get(key, default)


def get_default_cookie(path: str | Path = CONFIG_FILE) -> str:
    """读取服务器默认 Cookie 字符串（未配置/文件缺失返回空串，不抛异常）"""
    return get("server", "default_cookie", "", path) or ""


def save_default_cookie(cookie: str, path: str | Path = CONFIG_FILE) -> bool:
    """把 Cookie 字符串写回配置文件的 [server].default_cookie

    以文本替换方式只改这一行的值，保留文件其余内容与注释；
    键不存在时插入到 [server] 节头之后，无 [server] 节则在文件末尾追加。
    Cookie 值使用 json.dumps 编码（其转义规则是合法的 TOML 基本字符串）。

    Returns:
        是否写入成功
    """
    path = Path(path)
    try:
        if path.exists():
            text = path.read_text(encoding="utf-8")
        else:
            text = ""
    except OSError as e:
        print(f"读取配置文件失败: {e}", file=sys.stderr)
        return False

    value_line = f"default_cookie = {json.dumps(cookie, ensure_ascii=False)}"

    # 已有键：原地替换该行
    key_pattern = re.compile(r'(?m)^(?P<indent>\s*default_cookie\s*=\s*)".*"\s*$')
    if key_pattern.search(text):
        new_text = key_pattern.sub(
            lambda m: m.group("indent") + json.dumps(cookie, ensure_ascii=False),
            text,
            count=1,
        )
    else:
        section_pattern = re.compile(r"(?m)^\[server\]\s*$")
        if section_pattern.search(text):
            # 有 [server] 节但无该键：插到节头之后，避免产生重复节
            new_text = section_pattern.sub(f"[server]\n{value_line}", text, count=1)
        else:
            # 无 [server] 节：文件末尾追加
            sep = "" if (not text or text.endswith("\n\n")) else ("\n" if text.endswith("\n") else "\n\n")
            new_text = f"{text}{sep}[server]\n{value_line}\n"

    try:
        path.write_text(new_text, encoding="utf-8")
        return True
    except OSError as e:
        print(f"写入配置文件失败: {e}", file=sys.stderr)
        return False
