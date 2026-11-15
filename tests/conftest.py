"""pytest 全局配置：路径注入与跨文件公共 fixture

职责：
1. 将项目根目录注入 sys.path，测试文件可直接 import 业务模块
   （迁移前各测试文件自带 ROOT/sys.path 样板，现统一收敛于此）。
2. tmp fixture：每个测试独立的临时目录（即 pytest 内建 tmp_path），
   保留迁移前测试函数的 tmp 形参签名，bare assert 断言零改动。
3. 限速器每模块重置：迁移前自检脚本为「每文件独立进程」，slowapi 内存限速
   状态天然隔离；pytest 单进程收集全部文件后共享同一限速计数，若不重置，
   跨文件累计的 /download 请求会击穿阈值并破坏 test_fastapi_routes 的
   429 断言语义。此处按「模块（文件）切换」粒度重置，精确复刻原进程隔离。
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

_last_module: str | None = None


@pytest.fixture
def tmp(tmp_path: Path) -> Path:
    """测试级临时目录（迁移前 main() 手工 mkdtemp 的等价替代）"""
    return tmp_path


@pytest.fixture(autouse=True)
def _fresh_rate_limiter_per_module(request: pytest.FixtureRequest):
    """在每个测试文件的第一个测试前重置限速器内存存储，复刻原进程隔离语义"""
    global _last_module
    module = request.module.__name__
    if module != _last_module:
        _last_module = module
        import main

        main.limiter._storage.reset()
