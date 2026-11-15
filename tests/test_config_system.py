"""配置系统自检（pytest 管理）

覆盖范围：
1. config_loader 严格读取：文件缺失抛 FileNotFoundError、TOML 格式错误抛 TOMLDecodeError
2. 默认值合并：缺失键回落 DEFAULTS，已知配置项数值正确
3. get_config 宽松读取：文件缺失回落默认值
4. save_default_cookie：写入回环、重复覆盖、保留其余内容、
   [server] 节缺键时插入、无 [server] 节时追加（写入均作用于临时副本）
5. music_api.load_cookies：接入 config.toml 后解析出非空 Cookie
6. qr_login.QRLoginClient：读取/保存/备份/登出（作用于临时副本）
7. main.py：绑定地址/端口读自 config.toml

运行方式：python -m pytest tests/ -v
注意：涉及 Cookie 的测试只断言字段数量与回环一致性，不打印任何凭据内容。
"""

import tempfile
import tomllib
from pathlib import Path

import config_loader as cl
from music_api import load_cookies
from qr_login import QRLoginClient

ROOT = Path(__file__).resolve().parent.parent


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_strict_load_errors(tmp: Path) -> None:
    try:
        cl.load_config(tmp / "missing.toml")
        raise AssertionError("缺失文件未抛 FileNotFoundError")
    except FileNotFoundError:
        pass

    bad = _write(tmp / "bad.toml", "port = 不是数字\n[broken")
    try:
        cl.load_config(bad)
        raise AssertionError("非法 TOML 未抛 TOMLDecodeError")
    except tomllib.TOMLDecodeError:
        pass


def test_defaults_and_real_config() -> None:
    cfg = cl.get_config()  # 项目根 config.toml
    assert cfg["server"]["host"] == "127.0.0.1"
    assert cfg["server"]["port"] == 5000
    assert cfg["cache"]["max_size_mb"] == 8192
    assert isinstance(cfg["download"]["multi_thread"], bool)
    assert cfg["download"]["speed_limit_kb"] == 0
    assert cfg["api"]["rate_limit_per_minute"] == 10
    assert cfg["api"]["rate_limit_per_hour"] == 25

    # 宽松读取：缺失文件回落默认值
    missing = cl.get_config(Path(tempfile.mkdtemp()) / "nope.toml")
    assert missing["server"]["port"] == 5000
    assert cl.get_default_cookie(Path(tempfile.mkdtemp()) / "nope.toml") == ""


def test_save_roundtrip(tmp: Path) -> None:
    cfg = _write(tmp / "cfg.toml", (ROOT / "config.example.toml").read_text(encoding="utf-8"))

    # 写入回环
    assert cl.save_default_cookie("MUSIC_U=abc; os=pc", cfg)
    assert cl.get_default_cookie(cfg) == "MUSIC_U=abc; os=pc"

    # 覆盖写不残留旧值，且其余内容保留
    assert cl.save_default_cookie("MUSIC_U=xyz", cfg)
    text = cfg.read_text(encoding="utf-8")
    assert cl.get_default_cookie(cfg) == "MUSIC_U=xyz"
    assert "MUSIC_U=abc" not in text
    assert 'host = "127.0.0.1"' in text and "max_size_mb = 8192" in text
    tomllib.loads(text)  # 仍是合法 TOML

    # 清空写
    assert cl.save_default_cookie("", cfg)
    assert cl.get_default_cookie(cfg) == ""


def test_save_insertion_cases(tmp: Path) -> None:
    # [server] 节存在但缺 default_cookie 键 → 插入节内，不产生重复节
    cfg = _write(tmp / "no_key.toml", '[server]\nhost = "0.0.0.0"\nport = 8000\n')
    assert cl.save_default_cookie("MUSIC_U=k", cfg)
    parsed = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert parsed["server"]["default_cookie"] == "MUSIC_U=k"
    assert parsed["server"]["port"] == 8000

    # 完全无 [server] 节 → 文件末尾追加，文件仍合法
    cfg2 = _write(tmp / "no_section.toml", '[cache]\nmax_size_mb = 100\n')
    assert cl.save_default_cookie("MUSIC_U=j", cfg2)
    parsed2 = tomllib.loads(cfg2.read_text(encoding="utf-8"))
    assert parsed2["server"]["default_cookie"] == "MUSIC_U=j"
    assert parsed2["cache"]["max_size_mb"] == 100

    # 配置文件不存在 → 自动创建最小文件
    cfg3 = tmp / "created.toml"
    assert cl.save_default_cookie("MUSIC_U=i", cfg3)
    assert cl.get_default_cookie(cfg3) == "MUSIC_U=i"


def test_load_cookies_from_real_config() -> None:
    cookies = load_cookies()
    raw = cl.get_default_cookie()
    assert (len(cookies) > 0) == (len(raw.strip()) > 0)
    assert isinstance(cookies, dict)


def test_qr_login_client(tmp: Path) -> None:
    cfg = _write(tmp / "qr.toml", (ROOT / "config.example.toml").read_text(encoding="utf-8"))
    client = QRLoginClient(config_file=str(cfg))

    # 初始为空 → 无效、无备份
    assert not client._is_cookie_valid()
    assert client._backup_cookie() is None

    # 保存含关键字段的 Cookie → 有效
    assert client.save_cookie("MUSIC_U=testvalue; __csrf=token")
    assert client._is_cookie_valid()
    info = client._get_cookie_info()
    assert info["is_valid"] and info["cookie_count"] == 2 and info["config_exists"]

    # 再次保存 → 生成备份文件且不回显内容
    backup = client._backup_cookie()
    assert backup is not None and backup.exists() and backup.name.startswith("cookie.backup.backup.")
    assert backup.read_text(encoding="utf-8").strip()  # 备份内容非空（不断言具体值）

    # 登出 → 清空（先备份）
    assert client.logout()
    assert cl.get_default_cookie(cfg) == ""
    assert not client._is_cookie_valid()

    # 空 Cookie 拒绝写入
    assert not client._write_cookie("   ")


def test_main_wiring() -> None:
    import main  # noqa: F401  导入即完成配置接线
    assert main.config.host == cl.get_config()["server"]["host"]
    assert main.config.port == cl.get_config()["server"]["port"]


def test_gitignore_effective() -> None:
    from subprocess import run
    for f in ("config.toml",):
        res = run(["git", "check-ignore", f], cwd=ROOT, capture_output=True, text=True)
        assert res.returncode == 0, f"{f} 未被 .gitignore 覆盖"
    # 真实配置不得进入暂存区
    res = run(["git", "status", "--short"], cwd=ROOT, capture_output=True, text=True)
    assert "config.toml" not in res.stdout.replace("?? config.toml", ""), "config.toml 出现在暂存/跟踪区"
