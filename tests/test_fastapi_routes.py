"""FastAPI 公开路由自检（pytest 管理）

覆盖范围（不发起任何真实上游解析/下载）：
1. 大写别名路由已移除：/Song_V1 等 → 404 统一错误结构
2. 参数校验分支：/download 缺 id → 400 统一结构
3. 限速（slowapi）：超过每分钟阈值触发 429 统一结构（用缺参请求，不触上游）
4. /health 不在限速范围（监控可用性）且 CORS 头存在
5. 根路径托管静态页；/docs 自动文档可访问

运行方式：python -m pytest tests/ -v
"""

from fastapi.testclient import TestClient

import main

client = TestClient(main.app)


def test_alias_routes_removed() -> None:
    for path in ("/Song_V1", "/Search", "/Playlist", "/Album", "/Download"):
        r = client.post(path, json={})
        assert r.status_code == 404, f"{path} 应为 404，实际 {r.status_code}"
        body = r.json()
        assert body["status"] == 404 and body["success"] is False \
            and body["message"] == "请求的资源不存在", body


def test_validation_branch() -> None:
    r = client.post("/download", json={"quality": "lossless"})
    assert r.status_code == 400
    body = r.json()
    assert body["success"] is False and "music_id" in body["message"], body
    r2 = client.get("/song")  # 缺 ids/id/url
    assert r2.status_code == 400 and "必须提供" in r2.json()["message"], r2.json()


def test_rate_limit_429() -> None:
    """连发缺参 /download（400，不触上游）：分钟阈值（含参数校验用例的消耗）内应出现 429"""
    codes = []
    for _ in range(12):
        r = client.post("/download", json={"quality": "standard"})
        codes.append(r.status_code)
    assert codes[0] == 400, f"首次请求应为 400（阈值内）: {codes}"
    assert 429 in codes, f"应触发限速 429: {codes}"
    assert codes.index(429) >= 8, f"429 出现过早（阈值 10/分钟）: {codes}"


def test_429_body_structure() -> None:
    # 已超限，直接抓取一个 429 响应断言统一结构
    body = None
    for _ in range(5):
        r = client.post("/download", json={"quality": "standard"})
        if r.status_code == 429:
            body = r.json()
            break
    assert body is not None, "未捕获 429"
    assert body["status"] == 429 and body["success"] is False \
        and "请求过于频繁" in body["message"], body


def test_health_and_cors() -> None:
    r = client.get("/health")
    assert r.status_code == 200 and r.json()["success"] is True
    # CORS 中间件对带 Origin 的跨域请求返回允许头（同源请求不添加属正常行为）
    rc = client.get("/health", headers={"Origin": "http://example.com"})
    assert rc.headers.get("access-control-allow-origin") == "*", "CORS 跨域允许头缺失"


def test_root_and_docs() -> None:
    r = client.get("/")
    assert r.status_code == 200 and b"<html" in r.content[:512].lower()
    r2 = client.get("/docs")
    assert r2.status_code == 200
