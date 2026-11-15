"""React 前端构建与托管自检脚本（无测试框架，直接运行）

覆盖范围：
1. 构建产物存在且结构正确：frontend/dist/index.html 含 #root 挂载点与 /assets 资源引用
2. GET / 返回 200 HTML（含 root 挂载点），/assets 资源可访问（200）
3. dist 缺失分支：临时移走 dist 后 GET / 返回 503 提示构建（结束后恢复）

前置条件：frontend/ 已执行 npm install && npm run build（部署标准流程）。
运行方式：.venv/bin/python tests/test_react_frontend.py
"""

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DIST = ROOT / "frontend" / "dist"


def test_dist_structure() -> None:
    index_file = DIST / "index.html"
    assert index_file.is_file(), "dist/index.html 不存在（请先在 frontend/ 执行 npm run build）"
    html = index_file.read_text(encoding="utf-8")
    assert 'id="root"' in html, "index.html 缺少 #root 挂载点"
    assert "/assets/" in html, "index.html 未引用 /assets 构建资源"
    assets = list((DIST / "assets").glob("*.js"))
    assert assets, "assets 目录缺少 JS 产物"
    print(f"通过: 构建产物结构（assets JS {len(assets)} 个）")


def test_serving() -> None:
    import main
    from fastapi.testclient import TestClient
    client = TestClient(main.app)

    r = client.get("/")
    assert r.status_code == 200, f"GET / 应为 200: {r.status_code}"
    assert 'id="root"' in r.text, "首页未返回 React 挂载点"
    assert "text/html" in r.headers.get("content-type", "")

    js_files = list((DIST / "assets").glob("*.js"))
    r2 = client.get(f"/assets/{js_files[0].name}")
    assert r2.status_code == 200, f"/assets 资源不可访问: {r2.status_code}"
    print("通过: FastAPI 托管首页与 /assets 静态资源")


def test_missing_dist_branch() -> None:
    import main
    from fastapi.testclient import TestClient
    client = TestClient(main.app)

    moved = DIST.with_name("dist_moved_for_test")
    shutil.move(str(DIST), str(moved))
    try:
        # /assets 挂载在导入时已按目录存在性决定，此处仅验证 / 的 503 分支
        r = client.get("/")
        assert r.status_code == 503, f"dist 缺失应返回 503: {r.status_code}"
        assert "npm run build" in r.json()["message"]
    finally:
        shutil.move(str(moved), str(DIST))
    r2 = client.get("/")
    assert r2.status_code == 200, "恢复 dist 后首页应可用"
    print("通过: dist 缺失分支返回 503 构建提示（恢复后正常）")


def main() -> None:
    test_dist_structure()
    test_serving()
    test_missing_dist_branch()
    print("\n全部自检通过 ✔")


if __name__ == "__main__":
    main()
