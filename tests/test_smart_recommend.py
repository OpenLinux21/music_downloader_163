"""智能推荐自检脚本（无测试框架，直接运行）

覆盖范围：
1. 打分与排序：同歌手(+3)、名称近似(+相似度×2)、同专辑(+1)、热门兜底的组合排序
2. 数量约束：候选充足时恰好 5 首；自身被排除
3. 展示结构：每项 qualities 含 quality/ext/size/cached=True
4. 边界：目标无元数据→空；完全无缓存候选→空
5. API 集成：GET /recommend 结构、缺参 400、非法 ID 400（真实 storage，可重置种子）
6. 端到端（不触上游）：对真实缓存歌曲 /recommend 返回合法结构

运行方式：.venv/bin/python tests/test_smart_recommend.py
"""

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from storage_manager import StorageManager


def _register_song(sm: StorageManager, song_id: int, name: str, artists: str,
                   album_id, album_name: str, visit_count: int = 0,
                   qualities=(("standard", 111),)) -> None:
    sm.register_song({"song_id": song_id, "name": name, "artists": artists,
                      "album_id": album_id, "album_name": album_name, "duration": 200})
    with sm._connect() as conn:
        conn.execute("UPDATE songs SET visit_count = ? WHERE song_id = ?", (visit_count, song_id))
    for quality, size in qualities:
        f = sm.song_dir(song_id) / f"{name}.{quality}.mp3"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"\0" * size)
        sm.register_file(song_id, quality, f, size)


def test_scoring_and_order(tmp: Path) -> None:
    sm = StorageManager(tmp / "storage")
    _register_song(sm, 600, "晴天", "周杰伦", 7, "叶惠美", 3)                      # 目标
    _register_song(sm, 601, "七里香", "周杰伦", 8, "七里香", 10)                    # 同歌手 +3
    _register_song(sm, 604, "告白气球", "周杰伦", 9, "周杰伦的床边故事", 5)          # 同歌手 +3（访问量低，排 601 后）
    _register_song(sm, 602, "晴天版", "五月天", 7, "叶惠美", 1)                   # 名称近似 0.8×2 + 同专辑 1 = 2.6
    _register_song(sm, 603, "晴天", "林俊杰", None, "", 2)                          # 名称完全相同 +2.0
    _register_song(sm, 605, "稻香", "周杰伦之外", None, "", 50)                     # 零相关 → 热门兜底第 5
    _register_song(sm, 606, "无关A", "路人甲", None, "", 40)                        # 兜底池但访问量低于 605

    recs = sm.recommend_for(600, limit=5)
    ids = [r["song_id"] for r in recs]
    assert len(recs) == 5, f"应恰好 5 首: {ids}"
    assert 600 not in ids, "自身不应出现在推荐中"
    assert ids == [601, 604, 602, 603, 605], f"排序异常: {ids}"
    # 展示结构
    first = recs[0]
    assert first["name"] == "七里香" and first["artists"] == "周杰伦"
    q = first["qualities"]
    assert q and q[0]["quality"] == "standard" and q[0]["size"] == 111 and q[0]["cached"] is True
    print(f"通过: 打分排序 {ids}（同歌手>名称近似+同专辑>名称相同>热门兜底）与展示结构")


def test_edge_cases(tmp: Path) -> None:
    sm = StorageManager(tmp / "s_edge")
    assert sm.recommend_for(999) == [], "无元数据目标应返回空"
    _register_song(sm, 700, "孤歌", "独立歌手", None, "", 9)
    assert sm.recommend_for(700) == [], "无其他缓存候选应返回空"
    # 候选少于 limit：全部返回且不报错
    _register_song(sm, 701, "另一首", "独立歌手", None, "", 1)
    recs = sm.recommend_for(700, limit=5)
    assert len(recs) == 1 and recs[0]["song_id"] == 701
    print("通过: 边界（无元数据/无候选/候选不足补足）")


def test_api(tmp: Path) -> None:
    import main
    from fastapi.testclient import TestClient
    sm = main.api_service.storage
    song_id = 710
    # 重置种子保证幂等
    with sm._connect() as conn:
        conn.execute("DELETE FROM files WHERE song_id IN (710, 711)")
        conn.execute("DELETE FROM songs WHERE song_id IN (710, 711)")
    _register_song(sm, song_id, "API推荐目标", "歌手X", None, "", 1)
    _register_song(sm, 711, "API同歌手歌", "歌手X", None, "", 2)

    client = TestClient(main.app)
    r = client.get("/recommend", params={"song_id": str(song_id)})
    body = r.json()
    assert r.status_code == 200 and body["success"] is True, body
    recs = body["data"]["recommendations"]
    # 真实缓存池含其他歌曲：同歌手的 711 应排第一，总数 ≤5（可能被热门兜底补足）
    assert body["data"]["song_id"] == song_id
    assert 1 <= len(recs) <= 5 and recs[0]["song_id"] == 711, recs
    assert recs[0]["qualities"][0]["cached"] is True

    r2 = client.get("/recommend")
    assert r2.status_code == 400 and "不能为空" in r2.json()["message"]
    r3 = client.get("/recommend", params={"song_id": "not-a-number"})
    assert r3.status_code == 400 and "数字" in r3.json()["message"]
    print("通过: /recommend API（结构/缺参 400/非法 ID 400）")


def test_end_to_end(tmp: Path) -> None:
    """真实缓存池（不触上游）：对 347230 请求推荐，断言结构合法"""
    import main
    from fastapi.testclient import TestClient
    client = TestClient(main.app)
    r = client.get("/recommend", params={"song_id": "347230"})
    body = r.json()
    assert r.status_code == 200 and body["success"] is True
    recs = body["data"]["recommendations"]
    assert len(recs) <= 5
    for it in recs:
        assert it["song_id"] != 347230, "推荐包含自身"
        assert it["qualities"], f"推荐项 {it['song_id']} 无音质版本"
        for q in it["qualities"]:
            assert q["cached"] is True and q["size"] > 0
    print(f"通过: 端到端推荐（返回 {len(recs)} 首，全部为缓存歌曲并标注音质与大小）")


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="rectest_"))
    try:
        test_scoring_and_order(tmp)
        test_edge_cases(tmp)
        test_api(tmp)
        test_end_to_end(tmp)
        print("\n全部自检通过 ✔")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
